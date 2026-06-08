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
    external_id: str,
    due_date: str | None,
) -> str | None:
    """Create an Asana task and return its GID. Returns None if Asana is not configured."""
    if not ASANA_API_KEY or not ASANA_PROJECT_ID:
        return None

    webhook_url  = os.environ.get("WEBHOOK_URL", "")
    label_token  = os.environ.get("WEBHOOK_LABEL_TOKEN", "")
    outlook_url  = (
        "https://outlook.office.com/mail/inbox/id/"
        + urllib.parse.quote(external_id, safe="")
    )

    def action_url(label: str, source: str) -> str:
        params = f"id={message_id}&label={label}&source={source}"
        if label_token:
            params += f"&token={urllib.parse.quote(label_token, safe='')}"
        return f"{webhook_url}/label?{params}"

    notes = (
        f"From: {sender_display} ({sender})\n"
        f"Received: {received_at}\n"
        f"Importance: {importance}\n"
        f"Tags: {', '.join(tags) or 'none'}\n"
        f"\nAI reasoning: {reasoning}\n"
        f"\n{'─' * 35}\n"
        f"{body[:500]}{'...' if len(body) > 500 else ''}\n"
        f"\nOpen in Outlook → {outlook_url}\n"
        f"\n{'─' * 35}\n"
        f"Actions:\n"
        f"✓ Confirmed review  → {action_url('review', 'human_confirmation')}\n"
        f"↩ Respond instead   → {action_url('respond', 'human_correction')}\n"
        f"📁 Archive           → {action_url('reference', 'human_correction')}\n"
        f"🗑 Ignore            → {action_url('ignore', 'human_correction')}\n"
    )

    payload: dict = {
        "name": f"[{importance}] {subject or '(no subject)'}",
        "notes": notes,
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
