from models.message import Message
from models.types import Classification


def handle(classification: Classification, msg: Message) -> dict:
    """Review needs no inbox-side enrichment; tasks/schedule act on the event."""
    return {}
