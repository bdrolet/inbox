import logging

import clients.asana as asana
import clients.ntfy as ntfy
from clients.graph import get_graph_client
from models.message import Message
from models.types import Classification
from services import asana_tag_cache as tag_cache_svc
from services import email_summary as summary_svc

logger = logging.getLogger(__name__)


def handle(result: Classification, msg: Message) -> None:
    html_body: str | None = None
    try:
        email = get_graph_client().get_email_details(msg["external_id"])
        if email:
            html_body = email.body_content
    except Exception:
        logger.warning("Could not fetch HTML body for message_id=%s", msg["id"])

    try:
        tag_gids = tag_cache_svc.resolve_gids(result.tags)
    except Exception:
        logger.exception("Tag GID resolution failed for message_id=%s", msg["id"])
        tag_gids = []
    summary = summary_svc.generate(msg, html_body=html_body)

    task_url: str | None = None
    try:
        task = asana.create_task(
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
            due_date=None,
            category="urgent",
            tag_gids=tag_gids,
            summary=summary,
        )
        if task:
            task_url = task.permalink_url
        logger.info(
            "Urgent task created: gid=%s for message_id=%s", task.gid if task else None, msg["id"]
        )
    except Exception:
        logger.exception("Urgent task creation failed for message_id=%s", msg["id"])

    ntfy.notify(
        message_id=str(msg["id"] or ""),
        subject=msg["subject"],
        sender=msg["sender"],
        reasoning=result.reasoning,
        importance=result.importance.value,
        task_url=task_url,
    )
    logger.info("ntfy notification sent for message_id=%s", msg["id"])
