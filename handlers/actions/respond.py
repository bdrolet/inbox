import logging

import clients.asana as asana
from clients.graph import get_graph_client
from models.message import Message
from models.types import Classification
from services import archiving
from services import draft_reply as draft_svc
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

    moved = archiving.move_to_folder(msg, "reply_required")
    web_link = (moved or {}).get("webLink") or msg.get("web_link")
    try:
        tag_gids = tag_cache_svc.resolve_gids(result.tags)
    except Exception:
        logger.exception("Tag GID resolution failed for message_id=%s", msg["id"])
        tag_gids = []
    summary = summary_svc.generate(msg, html_body=html_body)

    try:
        draft_text = draft_svc.generate(msg)
        draft_link = get_graph_client().create_reply_draft(msg["external_id"], draft_text)
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
            due_date=None,
            category="respond",
            draft_link=draft_link,
            tag_gids=tag_gids,
            summary=summary,
        )
        logger.info(
            "Respond task created: gid=%s draft=%s for message_id=%s",
            task_gid, draft_link, msg["id"],
        )
    except Exception:
        logger.exception("Respond task/draft creation failed for message_id=%s", msg["id"])
