import logging

from handlers.actions._shared import prepare
from models.message import Message
from models.types import Classification
from services import calendar_invite as calendar_invite_svc

logger = logging.getLogger(__name__)


def handle(classification: Classification, msg: Message) -> None:
    _web_link, _summary, _due_date, invite = prepare(msg, classification, folder="Archive")
    if invite:
        calendar_invite_svc.store(invite)
