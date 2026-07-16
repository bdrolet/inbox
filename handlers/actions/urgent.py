import logging

import clients.ntfy as ntfy
from handlers.actions._shared import prepare
from models.message import Message
from models.types import Classification
from services import email_events
from services.links import redirector_url

logger = logging.getLogger(__name__)


def handle(classification: Classification, msg: Message) -> dict:
    invite = prepare(msg)
    points, links = email_events.invite_extras(str(msg["id"]), invite)

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
    return {"seed_key_points": points or None, "seed_links": links or None}
