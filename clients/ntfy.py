import os

import httpx

NTFY_BASE_URL = os.environ.get("NTFY_BASE_URL", "https://ntfy.sh")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")


def notify(message_id: str, subject: str, sender: str, reasoning: str, importance: str) -> None:
    if not NTFY_TOPIC:
        return

    webhook_url = os.environ["WEBHOOK_URL"]
    httpx.post(
        f"{NTFY_BASE_URL}/{NTFY_TOPIC}",
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
