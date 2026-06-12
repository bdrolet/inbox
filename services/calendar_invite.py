import base64
import logging
import re
from datetime import datetime, timezone

from icalendar import Calendar

from clients.db import get_conn
from models.message import Message
from models.types import CalendarInvite
import repo.calendar_invites as repo_cal

logger = logging.getLogger(__name__)

_ZOOM_RE = re.compile(r"https://[a-z0-9.]*zoom\.us/[^\s\"'<>]+")


def detect(msg: Message, graph_client) -> CalendarInvite | None:
    """Fetch attachments for msg via Graph API; parse .ics if present."""
    external_id = msg.get("external_id")
    if not external_id:
        return None

    try:
        attachments = graph_client.get_attachments(external_id)
    except Exception:
        logger.exception("Failed to fetch attachments for message %s", msg.get("id"))
        return None

    for att in attachments:
        content_type = (att.get("contentType") or "").lower()
        if "text/calendar" not in content_type and "ics" not in content_type:
            continue
        raw = att.get("contentBytes")
        if not raw:
            continue
        try:
            ical_bytes = base64.b64decode(raw)
            return _parse_ics(ical_bytes, msg)
        except Exception:
            logger.exception("Failed to parse .ics attachment for message %s", msg.get("id"))

    return None


def _parse_ics(ical_bytes: bytes, msg: Message) -> CalendarInvite | None:
    cal = Calendar.from_ical(ical_bytes)
    for component in cal.walk():
        if component.name != "VEVENT":
            continue

        uid = str(component.get("UID") or "")
        title = str(component.get("SUMMARY") or msg.get("subject") or "")
        organizer = str(component.get("ORGANIZER") or "").replace("mailto:", "")
        location = str(component.get("LOCATION") or "") or None
        description = str(component.get("DESCRIPTION") or "")

        dtstart = component.get("DTSTART")
        dtend = component.get("DTEND")
        if dtstart is None or dtend is None:
            logger.warning("VEVENT missing DTSTART/DTEND for message %s", msg.get("id"))
            return None

        start_dt = dtstart.dt
        end_dt = dtend.dt
        tzname = "UTC"
        if hasattr(start_dt, "tzinfo") and start_dt.tzinfo is not None:
            tzname = str(start_dt.tzinfo)
        elif hasattr(dtstart, "params") and "TZID" in dtstart.params:
            tzname = str(dtstart.params["TZID"])

        if not isinstance(start_dt, datetime):
            start_dt = datetime(start_dt.year, start_dt.month, start_dt.day, tzinfo=timezone.utc)
        if not isinstance(end_dt, datetime):
            end_dt = datetime(end_dt.year, end_dt.month, end_dt.day, tzinfo=timezone.utc)

        zoom_link: str | None = None
        for text in (location or "", description):
            m = _ZOOM_RE.search(text)
            if m:
                zoom_link = m.group(0)
                break

        return CalendarInvite(
            message_id=str(msg["id"]),
            graph_message_id=str(msg["external_id"]),
            ical_uid=uid,
            title=title,
            start=start_dt,
            end=end_dt,
            timezone=tzname,
            organizer=organizer,
            zoom_link=zoom_link,
            location=location,
        )

    return None


def store(invite: CalendarInvite) -> None:
    """Write CalendarInvite to DB. DB write only — task creation is the handler's job."""
    with get_conn() as conn:
        invite_id = repo_cal.insert(conn, invite)
        logger.info(
            "Stored calendar invite id=%s for message_id=%s ical_uid=%s",
            invite_id,
            invite.message_id,
            invite.ical_uid,
        )
