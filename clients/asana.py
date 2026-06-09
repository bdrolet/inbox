import os
import urllib.parse

import httpx

ASANA_API_KEY    = os.environ.get("ASANA_API_KEY", "")
ASANA_PROJECT_ID = os.environ.get("ASANA_PROJECT_ID", "")
_BASE            = "https://app.asana.com/api/1.0"


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

    html_notes = (
        "<body>"
        "<ul>"
        f"<li><strong>From:</strong> {esc(sender_display)} ({esc(sender)})</li>"
        f"<li><strong>Received:</strong> {esc(received_at)}</li>"
        f"<li><strong>Importance:</strong> {esc(importance)}</li>"
        f"<li><strong>Tags:</strong> {esc(', '.join(tags) or 'none')}</li>"
        "</ul>"
        f"<strong>AI reasoning:</strong> {esc(reasoning)}\n"
        f"\n{body_preview}\n"
        f"\n{outlook_link}"
        "\n<strong>Actions</strong>"
        "<ul>"
        f'<li><a href="{esc(action_url("review", "human_confirmation"))}">Confirmed review</a></li>'
        f'<li><a href="{esc(action_url("respond", "human_correction"))}">Respond instead</a></li>'
        f'<li><a href="{esc(action_url("reference", "human_correction"))}">Archive</a></li>'
        f'<li><a href="{esc(action_url("ignore", "human_correction"))}">Ignore</a></li>'
        "</ul>"
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

    resp = httpx.post(
        f"{_BASE}/tasks",
        headers={"Authorization": f"Bearer {ASANA_API_KEY}"},
        json={"data": payload},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["data"]["gid"]
