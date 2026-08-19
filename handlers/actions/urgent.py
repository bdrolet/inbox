import logging

import clients.ntfy as ntfy
from models.message import Message
from models.types import Classification
from services.links import redirector_url

logger = logging.getLogger(__name__)


def handle(classification: Classification, msg: Message) -> dict:
    try:
        ntfy.notify(
            message_id=str(msg["id"] or ""),
            subject=msg["subject"],
            sender=msg["sender"],
            reasoning=classification.reasoning,
            importance=classification.importance.value,
            # Task is created asynchronously by the tasks service — tapping the
            # push opens the email itself (stable redirector link) instead.
            click_url=redirector_url(str(msg.get("id") or "")) or msg.get("web_link"),
        )
        logger.info("ntfy notification sent for message_id=%s", msg["id"])
    except Exception:
        # A push failure must not cost the published event its email_classified emission.
        logger.exception("ntfy notification failed for message_id=%s", msg["id"])
    return {}
