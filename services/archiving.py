import logging

from clients.graph import get_graph_client
from models.message import Message
from models.types import Classification

logger = logging.getLogger(__name__)


def apply_tags(msg: Message, result: Classification) -> None:
    categories = [result.category.value, result.importance.value] + list(result.tags)
    ok = get_graph_client().tag_message(msg["external_id"], categories)
    if not ok:
        logger.warning("apply_tags failed for message_id=%s", msg["external_id"])
