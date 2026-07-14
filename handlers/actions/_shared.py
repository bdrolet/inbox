from clients.graph import get_graph_client
from models.message import Message
from models.types import CalendarInvite, Classification, EmailSummary, Importance
from services import calendar_invite as calendar_invite_svc
from services import deadline as deadline_svc
from services import email_summary as summary_svc
from services.links import redirector_url


def prepare(
    msg: Message,
    classification: Classification,
) -> tuple[str | None, EmailSummary, str | None, CalendarInvite | None]:
    """Detect calendar invite, generate summary + deadline, and resolve the link.

    The folder move is deferred to the morning sweep, so nothing is moved here.
    The returned link is a stable redirector URL (falls back to the raw webLink
    if REDIRECTOR_BASE_URL is unset).
    """
    invite = calendar_invite_svc.detect(msg, get_graph_client())
    if invite:
        calendar_invite_svc.store(invite)

    web_link = redirector_url(str(msg.get("id") or "")) or msg.get("web_link")

    summary = summary_svc.generate(msg, html_body=msg.get("body_html"))
    due_date = (
        deadline_svc.extract_deadline(msg)
        if classification.importance in (Importance.P0, Importance.P1)
        else None
    )
    return web_link, summary, due_date, invite
