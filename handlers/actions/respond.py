import logging

import clients.asana as asana
from clients.graph import get_graph_client
from handlers.actions._shared import prepare
from models.message import Message
from models.types import Classification
from services import calendar_invite as calendar_invite_svc
from services import draft_reply as draft_svc

logger = logging.getLogger(__name__)


def handle(classification: Classification, msg: Message) -> None:
    web_link, summary, due_date, invite = prepare(msg, classification, folder="reply_required")

    try:
        draft_text = draft_svc.generate(msg)
        draft_link = get_graph_client().create_reply_draft(msg["external_id"], draft_text)
        task = asana.create_task(
            msg,
            classification,
            web_link=web_link,
            due_date=due_date,
            draft_link=draft_link,
            summary=summary,
            invite=invite,
        )
        logger.info(
            "Respond task created: gid=%s draft=%s for message_id=%s",
            task.gid if task else None,
            draft_link,
            msg["id"],
        )
    except Exception:
        logger.exception("Respond task/draft creation failed for message_id=%s", msg["id"])

    if invite:
        calendar_invite_svc.store(invite)
