from datetime import datetime, timezone
from typing import Optional

from clients.azure.email import Email
from models.message import Message


def fetch(message_id: str, client) -> Optional[Email]:
    """Fetch a single email by ID from the Graph API."""
    return client.get_email_details(message_id)


def normalize(email: Email, raw: dict = None) -> Message:
    """Convert a Graph API Email object into the common Message shape."""
    received_at = email.received_datetime
    if not isinstance(received_at, datetime):
        received_at = datetime.now(timezone.utc)

    return Message(
        id=None,
        source="email",
        external_id=email.id,
        sender=email.from_email or "",
        sender_display=email.from_name or "",
        subject=email.subject or "",
        body=email.get_body_text(),
        received_at=received_at,
        thread_id=None,
        raw=raw or {},
        web_link=getattr(email, "web_link", None),
    )
