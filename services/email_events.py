"""Build and publish email domain events (the email-events topic).

One email_classified event per processed email (every category — the tasks
repo, github.com/bdrolet/tasks, owns the policy for which become Asana tasks)
plus label_applied feedback events. Tasks' models/events.py mirrors these
payloads exactly.

Calendar invites are not inbox's concern: the schedule repo
(github.com/bdrolet/schedule) owns all calendar logic and reads `.ics`
attachments via Graph itself, using the `graph_message_id`/`has_attachments`
fields below. `is_meeting_message` flags Exchange-native meeting
request/cancel/response messages, which carry no `.ics` attachment.
`seed_key_points`/`seed_links` remain generic hooks for handler extras.
"""

import logging
from datetime import datetime, timezone

import clients.otel as otel
from clients import pubsub
from models.message import Message
from models.types import Classification
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
    classification, and the action handler's extras (draft_link and any other
    handler extras)."""
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
        # Exchange-native meeting request/cancel/response (@odata.type
        # #microsoft.graph.eventMessage*) — no .ics attachment, so schedule
        # needs this hint to detect the meeting without relying on has_attachments.
        "is_meeting_message": bool(msg.get("is_meeting_message", False)),
    }


def email_sent_payload(email) -> dict:
    """email_sent: recipients + timestamp only (spec §10.3). No body, no inbox
    UUID (sent mail is never stored). Bcc deliberately excluded. Consumed by
    the people repo (github.com/bdrolet/people) for my_response_count."""
    sent = getattr(email, "sent_datetime", None)
    if isinstance(sent, datetime):
        sent_at = sent.isoformat()
    else:
        sent_at = datetime.now(timezone.utc).isoformat()

    def _addrs(recipients) -> list[str]:
        return [r["address"].strip().lower() for r in (recipients or []) if r.get("address")]

    return {
        "event": "email_sent",
        "graph_message_id": email.id or "",
        "conversation_id": getattr(email, "conversation_id", None),
        "sent_at": sent_at,
        "from": (email.from_email or "").lower(),
        "to": _addrs(email.to_recipients),
        "cc": _addrs(email.cc_recipients),
        "subject": email.subject or "",
    }
