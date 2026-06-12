import logging

import clients.asana as asana
import clients.ntfy as ntfy
from handlers.actions._shared import prepare
from models.message import Message
from models.types import Classification
from services import calendar_invite as calendar_invite_svc

logger = logging.getLogger(__name__)


def handle(classification: Classification, msg: Message) -> None:
    web_link, summary, due_date, invite = prepare(msg, classification)

    task_url: str | None = None
    try:
        task = asana.create_task(
            msg,
            classification,
            web_link=web_link,
            due_date=due_date,
            summary=summary,
            invite=invite,
        )
        if task:
            task_url = task.permalink_url
        logger.info(
            "Urgent task created: gid=%s for message_id=%s", task.gid if task else None, msg["id"]
        )
    except Exception:
        logger.exception("Urgent task creation failed for message_id=%s", msg["id"])

    if invite:
        calendar_invite_svc.store(invite)

    ntfy.notify(
        message_id=str(msg["id"] or ""),
        subject=msg["subject"],
        sender=msg["sender"],
        reasoning=classification.reasoning,
        importance=classification.importance.value,
        task_url=task_url,
    )
    logger.info("ntfy notification sent for message_id=%s", msg["id"])
