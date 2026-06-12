from models.message import Message
from models.types import Classification, EmailSummary, Importance
from services import archiving
from services import deadline as deadline_svc
from services import email_summary as summary_svc


def prepare(
    msg: Message,
    classification: Classification,
    folder: str | None = None,
) -> tuple[str | None, EmailSummary, str | None]:
    """Move to folder (if given), generate summary, extract deadline."""
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
    return web_link, summary, due_date
