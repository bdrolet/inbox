from clients.graph import get_graph_client
from models.message import Message
from models.types import CalendarInvite, Classification, EmailSummary, Importance
from services import archiving
from services import calendar_invite as calendar_invite_svc
from services import deadline as deadline_svc
from services import email_summary as summary_svc


def prepare(
    msg: Message,
    classification: Classification,
    folder: str | None = None,
) -> tuple[str | None, EmailSummary, str | None, CalendarInvite | None]:
    """Move to folder (if given), generate summary, extract deadline, detect calendar invite."""
    web_link = msg.get("web_link")
    if folder:
        moved = archiving.move_to_folder(msg, folder)
        web_link = (moved or {}).get("webLink") or web_link

    summary = summary_svc.generate(msg, html_body=msg.get("body_html"))
    due_date = (
        deadline_svc.extract_deadline(msg)
        if classification.importance in (Importance.P0, Importance.P1)
        else None
    )
    invite = calendar_invite_svc.detect(msg, get_graph_client())
    return web_link, summary, due_date, invite
