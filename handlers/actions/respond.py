import logging

from clients.graph import get_graph_client
from handlers.actions._shared import prepare
from models.message import Message
from models.types import Classification
from services import draft_reply as draft_svc
from services import email_events

logger = logging.getLogger(__name__)


def handle(classification: Classification, msg: Message) -> dict:
    invite = prepare(msg)
    points, links = email_events.invite_extras(str(msg["id"]), invite)
    extras: dict = {"seed_key_points": points or None, "seed_links": links or None}

    try:
        draft_text = draft_svc.generate(msg)
        extras["draft_link"] = get_graph_client().create_reply_draft(msg["external_id"], draft_text)
    except Exception:
        logger.exception("Draft generation failed for message_id=%s", msg["id"])
    return extras
