from clients.graph import get_graph_client
from models.message import Message
from models.types import CalendarInvite
from services import calendar_invite as calendar_invite_svc


def prepare(msg: Message) -> CalendarInvite | None:
    """Detect and store a calendar invite (RSVP flow stays in inbox). Summary
    and deadline enrichment now live in the tasks repo."""
    invite = calendar_invite_svc.detect(msg, get_graph_client())
    if invite:
        calendar_invite_svc.store(invite)
    return invite
