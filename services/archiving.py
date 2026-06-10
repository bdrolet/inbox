import logging

from clients.graph import get_graph_client
from models.message import Message
from models.types import Classification

logger = logging.getLogger(__name__)


def move_to_folder(msg: Message, folder_name: str) -> dict | None:
    moved = get_graph_client().move_message_to_action_folder(msg["external_id"], folder_name)
    if moved is None:
        logger.warning(
            "move_to_folder failed: message_id=%s folder=%s", msg["external_id"], folder_name
        )
    return moved


def apply_tags(msg: Message, result: Classification) -> None:
    categories = [result.category.value, result.importance.value] + list(result.tags)
    ok = get_graph_client().tag_message(msg["external_id"], categories)
    if not ok:
        logger.warning("apply_tags failed for message_id=%s", msg["external_id"])
