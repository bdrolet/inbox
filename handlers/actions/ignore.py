import logging

from services import archiving
from models.message import Message
from models.types import Classification

logger = logging.getLogger(__name__)


def handle(result: Classification, msg: Message) -> None:
    archiving.move_to_folder(msg, "Archive")
