import logging
from datetime import datetime, timezone
from typing import Optional

import requests

from clients.azure.email import Email
from models.message import Message

logger = logging.getLogger(__name__)


def fetch(message_id: str, client) -> Optional[Email]:
    """Fetch a single email by ID from the Graph API.

    Returns None on any Graph failure — the pipeline treats fetch failures
    as skips (pre-existing behavior, preserved when the client started
    raising instead of swallowing errors).
    """
    try:
        return client.get_email_details(message_id)
    except requests.RequestException:
        logger.exception("Failed to fetch message %s", message_id)
        return None


def normalize(email: Email, raw: dict | None = None) -> Message:
    """Convert a Graph API Email object into the common Message shape."""
    received_at = email.received_datetime
    if not isinstance(received_at, datetime):
        received_at = datetime.now(timezone.utc)

    return Message(
        id=None,
        source="email",
        external_id=email.id or "",
        sender=email.from_email or "",
        sender_display=email.from_name or "",
        subject=(email.subject or "").removeprefix("[LOCAL-TEST] "),
        body=email.get_body_text(),
        body_html=email.body_content if email.body_type == "html" else None,
        received_at=received_at,
        thread_id=None,
        raw=raw or {},
        web_link=getattr(email, "web_link", None),
    )
