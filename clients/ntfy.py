import os

import httpx

NTFY_BASE_URL = os.environ.get("NTFY_BASE_URL", "https://ntfy.sh")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
NTFY_TOKEN = os.environ.get("NTFY_TOKEN", "")


def notify(message_id: str, subject: str, sender: str, reasoning: str, importance: str) -> None:
    if not NTFY_TOPIC:
        return

    headers = {}
    if NTFY_TOKEN:
        headers["Authorization"] = f"Bearer {NTFY_TOKEN}"

    webhook_url = os.environ["WEBHOOK_URL"]
    httpx.post(
        f"{NTFY_BASE_URL}/{NTFY_TOPIC}",
        headers=headers,
        json={
            "topic": NTFY_TOPIC,
            "title": f"[{importance.upper()}] {subject}",
            "message": f"From: {sender}\n\n{reasoning}",
            "actions": [
                {"action": "http", "label": "Correct", "url": f"{webhook_url}/label?id={message_id}&label=urgent&source=human_confirmation"},
                {"action": "http", "label": "Respond", "url": f"{webhook_url}/label?id={message_id}&label=respond&source=human_correction"},
                {"action": "http", "label": "Review",  "url": f"{webhook_url}/label?id={message_id}&label=review&source=human_correction"},
            ],
        },
        timeout=10,
    )
