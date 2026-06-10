import os
import urllib.parse

import httpx

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
    message_id: str,
    subject: str,
    sender: str,
    sender_display: str,
    received_at: str,
    importance: str,
    tags: list[str],
    reasoning: str,
    body: str,
    web_link: str | None,
    due_date: str | None,
    category: str = "review",
    draft_link: str | None = None,
    tag_gids: list[str] | None = None,
) -> str | None:
    """Create an Asana task and return its GID. Returns None if Asana is not configured."""
    if not ASANA_API_KEY or not ASANA_PROJECT_ID:
        return None

    webhook_url = os.environ.get("WEBHOOK_URL", "")
    label_token = os.environ.get("WEBHOOK_LABEL_TOKEN", "")

    def action_url(label: str, source: str) -> str:
        params = f"id={message_id}&label={label}&source={source}"
        if label_token:
            params += f"&token={urllib.parse.quote(label_token, safe='')}"
        return f"{webhook_url}/label?{params}"

    def esc(text: str) -> str:
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    body_preview = esc(body[:500]) + ("..." if len(body) > 500 else "")
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
        f"\n{body_preview}\n"
        f"\n{outlook_link}"
        "\n<strong>Actions</strong>"
        f"<ul>{action_items}</ul>"
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
    if tag_gids:
        payload["tags"] = tag_gids

    resp = httpx.post(
        f"{_BASE}/tasks",
        headers={"Authorization": f"Bearer {ASANA_API_KEY}"},
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
    return resp.json()["data"]["gid"]
