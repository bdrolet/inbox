import logging

import clients.ntfy as ntfy
from models.message import Message
from models.types import Classification

logger = logging.getLogger(__name__)


def handle(result: Classification, msg: Message) -> None:
    ntfy.notify(
        message_id=msg["id"] or "",
        subject=msg["subject"],
        sender=msg["sender"],
        reasoning=result.reasoning,
        importance=result.importance.value,
    )
    logger.info("ntfy notification sent for message_id=%s", msg["id"])
