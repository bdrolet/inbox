import logging

import clients.asana as asana
from models.message import Message
from models.types import Classification, Importance
from services import archiving
from services import asana_tag_cache as tag_cache_svc
from services import deadline as deadline_svc

logger = logging.getLogger(__name__)


def handle(result: Classification, msg: Message) -> None:
    moved = archiving.move_to_folder(msg, "review")
    web_link = (moved or {}).get("webLink") or msg.get("web_link")
    try:
        tag_gids = tag_cache_svc.resolve_gids(result.tags)
    except Exception:
        logger.exception("Tag GID resolution failed for message_id=%s", msg["id"])
        tag_gids = []
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
            web_link=web_link,
            due_date=due_date,
            tag_gids=tag_gids,
        )
        logger.info("Asana task created: gid=%s due=%s for message_id=%s", task_gid, due_date, msg["id"])
    except Exception:
        logger.exception("Asana task creation failed for message_id=%s", msg["id"])
