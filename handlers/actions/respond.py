import logging

from clients.graph import get_graph_client
from models.message import Message
from models.types import Classification
from services import draft_reply as draft_svc

logger = logging.getLogger(__name__)


def handle(classification: Classification, msg: Message) -> dict:
    extras: dict = {}

    try:
        draft_text = draft_svc.generate(msg)
        extras["draft_link"] = get_graph_client().create_reply_draft(msg["external_id"], draft_text)
    except Exception:
        logger.exception("Draft generation failed for message_id=%s", msg["id"])
    return extras
