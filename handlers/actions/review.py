import logging

import clients.asana as asana
from handlers.actions._shared import prepare
from models.message import Message
from models.types import Classification

logger = logging.getLogger(__name__)


def handle(classification: Classification, msg: Message) -> None:
    web_link, summary, due_date = prepare(msg, classification, folder="review")

    try:
        task = asana.create_task(
            msg,
            classification,
            web_link=web_link,
            due_date=due_date,
            summary=summary,
        )
        logger.info(
            "Asana task created: gid=%s due=%s for message_id=%s",
            task.gid if task else None,
            due_date,
            msg["id"],
        )
    except Exception:
        logger.exception("Asana task creation failed for message_id=%s", msg["id"])
