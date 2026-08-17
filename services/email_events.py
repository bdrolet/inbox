"""Build and publish email domain events (the email-events topic).

One email_classified event per processed email (every category — the tasks
repo, github.com/bdrolet/tasks, owns the policy for which become Asana tasks)
plus label_applied feedback events. Tasks' models/events.py mirrors these
payloads exactly.

Calendar invites are deliberately NOT a dedicated payload field: invite facts
travel as seed_key_points and the RSVP/calendar links as seed_links
(invite_extras below), so the tasks service renders them with zero
calendar-specific code.

`graph_message_id` + `has_attachments` exist for the schedule repo
(github.com/bdrolet/schedule), which owns all calendar logic and reads
`.ics` attachments via Graph itself.
"""

import logging
import os
import urllib.parse
from datetime import datetime

import clients.otel as otel
from clients import pubsub
from models.message import Message
from models.types import CalendarInvite, Classification
from services.links import redirector_url

logger = logging.getLogger(__name__)

_TOPIC = "email-events"

# Defensive truncation — Pub/Sub caps messages at 10MB; enrichment in tasks
# reads at most the first 3000 chars of body and parses body_html for links.
_BODY_LIMIT = 10_000
_HTML_LIMIT = 200_000


def publish(event: dict) -> None:
    attrs = {"event": event.get("event") or "unknown"}
    try:
        pubsub.publish(_TOPIC, event)
    except Exception:
        otel.events_published.add(1, attrs | {"outcome": "error"})
        raise
    otel.events_published.add(1, attrs | {"outcome": "ok"})
    logger.info("Published %s event for message_id=%s", event.get("event"), event.get("message_id"))


def build_event(msg: Message, classification: Classification, extras: dict | None = None) -> dict:
    """Assemble the email_classified payload from the message, its
    classification, and the action handler's extras (draft_link, invite seeds)."""
    extras = extras or {}
    received = msg["received_at"]
    return {
        "event": "email_classified",
        "message_id": str(msg.get("id") or ""),
        "category": classification.category.value,
        "importance": classification.importance.value,
        "confidence": classification.confidence,
        "subject": msg["subject"],
        "sender": msg["sender"],
        "sender_display": msg.get("sender_display") or msg["sender"],
        "to": msg.get("to") or [],
        "cc": msg.get("cc") or [],
        # Cross-repo seam: pin the ISO-8601 "T" form (str(datetime) uses a space)
        "received_at": received.isoformat() if isinstance(received, datetime) else str(received),
        "tags": classification.tags,
        "reasoning": classification.reasoning,
        "body": (msg["body"] or "")[:_BODY_LIMIT],
        "body_html": (msg.get("body_html") or "")[:_HTML_LIMIT] or None,
        "web_link": redirector_url(str(msg.get("id") or "")) or msg.get("web_link"),
        "draft_link": extras.get("draft_link"),
        "seed_key_points": extras.get("seed_key_points"),
        "seed_links": extras.get("seed_links"),
        # Schedule fetches .ics attachments itself (owns all calendar logic):
        # immutable Graph id + a cheap gate so it only calls Graph when needed.
        "graph_message_id": msg["external_id"],
        "has_attachments": bool(msg.get("has_attachments", False)),
    }


def invite_extras(
    message_id: str, invite: CalendarInvite | None
) -> tuple[list[str], list[list[str]]]:
    """Fold a calendar invite into generic task_create fields.

    Returns (key_points lines, [url, label] links) to append to the event's
    key_points and relevant_links. RSVP links hit inbox's webhook /calendar
    endpoint (GET, token-authenticated) — same URLs the old Asana calendar
    block used.
    """
    if invite is None:
        return [], []

    start = invite.start.strftime("%Y-%m-%d %H:%M %Z") if invite.start else ""
    end = invite.end.strftime("%H:%M %Z") if invite.end else ""
    points = [
        f"Calendar invite: {invite.title or '(untitled)'} — {start}–{end}, "
        f"organizer {invite.organizer or 'unknown'}"
    ]
    if invite.location:
        points.append(f"Location: {invite.location}")

    webhook_url = os.environ.get("WEBHOOK_URL", "")
    label_token = os.environ.get("WEBHOOK_LABEL_TOKEN", "")

    def cal_url(action: str) -> str:
        params = f"id={message_id}&action={action}"
        if label_token:
            params += f"&token={urllib.parse.quote(label_token, safe='')}"
        return f"{webhook_url}/calendar?{params}"

    links: list[list[str]] = []
    if invite.zoom_link:
        links.append([invite.zoom_link, "Join Zoom"])
    gcal = (
        "https://calendar.google.com/calendar/render?action=TEMPLATE"
        f"&text={urllib.parse.quote(invite.title or '')}"
        f"&dates={invite.start.strftime('%Y%m%dT%H%M%SZ') if invite.start else ''}"
        f"/{invite.end.strftime('%Y%m%dT%H%M%SZ') if invite.end else ''}"
        f"&location={urllib.parse.quote(invite.location or invite.zoom_link or '')}"
    )
    links.append([gcal, "Open in Google Calendar"])
    links.append([cal_url("accept"), "RSVP: Accept"])
    links.append([cal_url("decline"), "RSVP: Decline"])
    links.append([cal_url("maybe"), "RSVP: Maybe"])
    return points, links
