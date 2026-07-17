from handlers.actions._shared import prepare
from models.message import Message
from models.types import Classification
from services import email_events


def handle(classification: Classification, msg: Message) -> dict:
    invite = prepare(msg)
    points, links = email_events.invite_extras(str(msg["id"]), invite)
    return {"seed_key_points": points or None, "seed_links": links or None}
