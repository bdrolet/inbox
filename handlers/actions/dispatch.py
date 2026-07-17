import logging

from handlers.actions import respond, review, urgent
from models.message import Message
from models.types import Category, Classification
from services import archiving, email_events

logger = logging.getLogger(__name__)

_HANDLERS = {
    Category.URGENT: urgent.handle,
    Category.RESPOND: respond.handle,
    Category.REVIEW: review.handle,
}


def dispatch(classification: Classification, msg: Message) -> None:
    logger.info(
        "Dispatching %s (importance=%s) for message_id=%s",
        classification.category.value,
        classification.importance.value,
        msg.get("id"),
    )
    try:
        archiving.apply_tags(msg, classification)
    except Exception:
        logger.exception("apply_tags failed for %s", msg.get("id"))

    extras: dict = {}
    handler = _HANDLERS.get(classification.category)
    if handler:
        try:
            extras = handler(classification, msg) or {}
        except Exception:
            logger.exception(
                "Action handler failed for %s/%s", classification.category.value, msg.get("id")
            )

    try:
        email_events.publish(email_events.build_event(msg, classification, extras))
    except Exception:
        logger.exception("email_classified publish failed for %s", msg.get("id"))
