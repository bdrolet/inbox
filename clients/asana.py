import os
import urllib.parse

import httpx

from models.message import Message
from models.types import CalendarInvite, Classification, CreatedTask, EmailSummary

ASANA_API_KEY = os.environ.get("ASANA_API_KEY", "")
ASANA_PROJECT_ID = os.environ.get("ASANA_PROJECT_ID", "")
_BASE = "https://app.asana.com/api/1.0"

_workspace_gid: str | None = None


def _get_workspace_gid() -> str:
    global _workspace_gid
    if not _workspace_gid:
        resp = httpx.get(
            f"{_BASE}/projects/{ASANA_PROJECT_ID}",
            params={"opt_fields": "workspace"},
            headers={"Authorization": f"Bearer {ASANA_API_KEY}"},
            timeout=10,
        )
        resp.raise_for_status()
        _workspace_gid = resp.json()["data"]["workspace"]["gid"]
    return _workspace_gid


def _find_tag(name: str, workspace_gid: str) -> str | None:
    """Search workspace tags by name via typeahead; return GID or None."""
    resp = httpx.get(
        f"{_BASE}/workspaces/{workspace_gid}/typeahead",
        params={"resource_type": "tag", "query": name},
        headers={"Authorization": f"Bearer {ASANA_API_KEY}"},
        timeout=10,
    )
    resp.raise_for_status()
    for item in resp.json().get("data", []):
        if item.get("name", "").casefold() == name.casefold():
            return item["gid"]
    return None


def _create_tag(name: str, workspace_gid: str) -> str:
    """Create a new tag in the workspace; return its GID."""
    resp = httpx.post(
        f"{_BASE}/tags",
        headers={"Authorization": f"Bearer {ASANA_API_KEY}"},
        json={"data": {"name": name, "workspace": workspace_gid}},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["data"]["gid"]


def create_task(
    msg: Message,
    result: Classification,
    *,
    web_link: str | None = None,
    due_date: str | None = None,
    draft_link: str | None = None,
    summary: EmailSummary | None = None,
    invite: CalendarInvite | None = None,
) -> CreatedTask | None:
    """Create an Asana task. Returns None if Asana is not configured."""
    if not ASANA_API_KEY or not ASANA_PROJECT_ID:
        return None

    message_id = str(msg["id"])
    subject = msg["subject"]
    sender = msg["sender"]
    sender_display = msg.get("sender_display") or msg["sender"]
    received_at = str(msg["received_at"])
    category = result.category.value
    importance = result.importance.value
    tags = result.tags
    reasoning = result.reasoning
    body = msg["body"] or ""

    webhook_url = os.environ.get("WEBHOOK_URL", "")
    label_token = os.environ.get("WEBHOOK_LABEL_TOKEN", "")

    def action_url(label: str, source: str) -> str:
        params = f"id={message_id}&label={label}&source={source}"
        if label_token:
            params += f"&token={urllib.parse.quote(label_token, safe='')}"
        return f"{webhook_url}/label?{params}"

    def esc(text: str) -> str:
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    outlook_link = f'<a href="{esc(web_link)}">Open in Outlook</a>\n' if web_link else ""

    if category == "respond":
        confirm_label, confirm_text = "respond", "Confirmed respond"
        alt_label, alt_text = "review", "Review instead"
    else:
        confirm_label, confirm_text = "review", "Confirmed review"
        alt_label, alt_text = "respond", "Respond instead"

    action_items = (
        f'<li><a href="{esc(action_url(confirm_label, "human_confirmation"))}">{confirm_text}</a></li>'
        f'<li><a href="{esc(action_url(alt_label, "human_correction"))}">{alt_text}</a></li>'
        f'<li><a href="{esc(action_url("reference", "human_correction"))}">Reference</a></li>'
        f'<li><a href="{esc(action_url("ignore", "human_correction"))}">Ignore</a></li>'
    )

    draft_item = (
        f'<li><a href="{esc(draft_link)}">Open draft reply in Outlook</a></li>'
        if draft_link
        else ""
    )

    if summary and summary.key_points:
        key_points_html = (
            "<strong>Key points:</strong><ul>"
            + "".join(f"<li>{esc(p)}</li>" for p in summary.key_points)
            + "</ul>"
        )
    else:
        body_preview = esc(body[:500]) + ("..." if len(body) > 500 else "")
        key_points_html = f"<strong>Preview:</strong>\n{body_preview}\n"

    if summary and summary.relevant_links:
        links_html = (
            "<strong>Links:</strong><ul>"
            + "".join(
                f'<li><a href="{esc(url)}">{esc(label)}</a></li>'
                for url, label in summary.relevant_links
            )
            + "</ul>"
        )
    else:
        links_html = ""

    calendar_html = ""
    if invite:
        webhook_url = os.environ.get("WEBHOOK_URL", "")
        label_token = os.environ.get("WEBHOOK_LABEL_TOKEN", "")

        def cal_url(action: str) -> str:
            params = f"id={message_id}&action={action}"
            if label_token:
                params += f"&token={urllib.parse.quote(label_token, safe='')}"
            return f"{webhook_url}/calendar?{params}"

        start_str = invite.start.strftime("%Y-%m-%d %H:%M %Z") if invite.start else ""
        end_str = invite.end.strftime("%H:%M %Z") if invite.end else ""
        gcal_template = (
            "https://calendar.google.com/calendar/render?action=TEMPLATE"
            f"&text={urllib.parse.quote(invite.title or '')}"
            f"&dates={invite.start.strftime('%Y%m%dT%H%M%SZ') if invite.start else ''}"
            f"/{invite.end.strftime('%Y%m%dT%H%M%SZ') if invite.end else ''}"
            f"&location={urllib.parse.quote(invite.location or invite.zoom_link or '')}"
        )
        zoom_item = (
            f'<li><a href="{esc(invite.zoom_link)}">Join Zoom</a></li>'
            if invite.zoom_link
            else ""
        )
        calendar_html = (
            "\n<strong>Calendar Invite</strong>"
            "<ul>"
            f"<li><strong>When:</strong> {esc(start_str)} – {esc(end_str)}</li>"
            f"<li><strong>Organizer:</strong> {esc(invite.organizer or '')}</li>"
            + (f"<li><strong>Location:</strong> {esc(invite.location)}</li>" if invite.location else "")
            + zoom_item
            + f'<li><a href="{esc(gcal_template)}">Open in Google Calendar</a></li>'
            + "</ul>"
            "<strong>RSVP</strong><ul>"
            f'<li><a href="{esc(cal_url("accept"))}">Accept</a></li>'
            f'<li><a href="{esc(cal_url("decline"))}">Decline</a></li>'
            f'<li><a href="{esc(cal_url("maybe"))}">Maybe</a></li>'
            "</ul>"
        )

    html_notes = (
        "<body>"
        "<ul>"
        f"<li><strong>From:</strong> {esc(sender_display)} ({esc(sender)})</li>"
        f"<li><strong>Received:</strong> {esc(received_at)}</li>"
        f"<li><strong>Importance:</strong> {esc(importance)}</li>"
        f"<li><strong>Tags:</strong> {esc(', '.join(tags) or 'none')}</li>"
        f"{draft_item}"
        "</ul>"
        f"<strong>AI reasoning:</strong> {esc(reasoning)}\n"
        f"\n{key_points_html}"
        f"\n{links_html}"
        f"\n{outlook_link}"
        "\n<strong>Actions</strong>"
        f"<ul>{action_items}</ul>"
        f"{calendar_html}"
        "</body>"
    )

    payload: dict = {
        "name": f"[{importance}] {subject or '(no subject)'}",
        "html_notes": html_notes,
        "projects": [ASANA_PROJECT_ID],
        "external": {"gid": message_id, "data": "inbox"},
    }
    if due_date:
        payload["due_on"] = due_date
    if result.tag_gids:
        payload["tags"] = result.tag_gids

    resp = httpx.post(
        f"{_BASE}/tasks",
        headers={"Authorization": f"Bearer {ASANA_API_KEY}"},
        params={"opt_fields": "gid,permalink_url"},
        json={"data": payload},
        timeout=10,
    )
    if resp.status_code == 400:
        errors = resp.json().get("errors", [])
        if any("already assigned" in e.get("message", "") for e in errors):
            import logging

            logging.getLogger(__name__).warning(
                "Asana task for message_id=%s already exists (duplicate external.gid) — skipping",
                message_id,
            )
            return None
    resp.raise_for_status()
    data = resp.json()["data"]
    return CreatedTask(gid=data["gid"], permalink_url=data["permalink_url"])
