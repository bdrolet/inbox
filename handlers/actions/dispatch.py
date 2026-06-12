import logging

from handlers.actions import ignore, reference, respond, review, urgent
from models.message import Message
from models.types import Category, Classification
from services import archiving

logger = logging.getLogger(__name__)

_HANDLERS = {
    Category.URGENT: urgent.handle,
    Category.RESPOND: respond.handle,
    Category.REVIEW: review.handle,
    Category.REFERENCE: reference.handle,
    Category.IGNORE: ignore.handle,
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
    handler = _HANDLERS.get(classification.category)
    if handler:
        try:
            handler(classification, msg)
        except Exception:
            logger.exception(
                "Action handler failed for %s/%s", classification.category.value, msg.get("id")
            )
