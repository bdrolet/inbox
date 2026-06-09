import logging

import clients.asana as asana
from models.message import Message
from models.types import Classification, Importance
from services import archiving
from services import deadline as deadline_svc

logger = logging.getLogger(__name__)


def handle(result: Classification, msg: Message) -> None:
    archiving.move_to_folder(msg, "review")
    try:
        due_date = None
        if result.importance in (Importance.P0, Importance.P1):
            due_date = deadline_svc.extract_deadline(msg)
        task_gid = asana.create_task(
            message_id=str(msg["id"]),
            subject=msg["subject"],
            sender=msg["sender"],
            sender_display=msg.get("sender_display") or msg["sender"],
            received_at=str(msg["received_at"]),
            importance=result.importance.value,
            tags=result.tags,
            reasoning=result.reasoning,
            body=msg["body"] or "",
            web_link=msg.get("web_link"),
            due_date=due_date,
        )
        logger.info("Asana task created: gid=%s due=%s for message_id=%s", task_gid, due_date, msg["id"])
    except Exception:
        logger.exception("Asana task creation failed for message_id=%s", msg["id"])
