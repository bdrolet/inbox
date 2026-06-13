import logging

from handlers.actions._shared import prepare
from models.message import Message
from models.types import Classification

logger = logging.getLogger(__name__)


def handle(classification: Classification, msg: Message) -> None:
    prepare(msg, classification, folder="Archive")
