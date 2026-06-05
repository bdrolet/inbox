import logging

from models.message import Message
from models.types import Category, Classification
from handlers.actions import urgent, respond, review, reference, ignore
from services import archiving

logger = logging.getLogger(__name__)

_HANDLERS = {
    Category.URGENT:    urgent.handle,
    Category.RESPOND:   respond.handle,
    Category.REVIEW:    review.handle,
    Category.REFERENCE: reference.handle,
    Category.IGNORE:    ignore.handle,
}


def dispatch(result: Classification, msg: Message) -> None:
    logger.info(
        "Dispatching %s (importance=%s) for message_id=%s",
        result.category.value, result.importance.value, msg.get("id"),
    )
    try:
        archiving.apply_tags(msg, result)
    except Exception:
        logger.exception("apply_tags failed for %s", msg.get("id"))
    handler = _HANDLERS.get(result.category)
    if handler:
        try:
            handler(result, msg)
        except Exception:
            logger.exception("Action handler failed for %s/%s", result.category.value, msg.get("id"))
