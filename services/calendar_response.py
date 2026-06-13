import logging

import clients.google_calendar as gcal
import repo.calendar_invites as repo_cal
from clients.db import get_conn
from clients.graph import get_graph_client

logger = logging.getLogger(__name__)


def apply(message_id: str, action: str) -> None:
    """
    Respond to a calendar invite action (accept | decline | maybe).

    - Sends the RSVP via Graph API
    - On accept: adds the event to Google Calendar
    - Updates user_response in DB
    """
    with get_conn() as conn:
        row = repo_cal.get_by_message_id(conn, message_id)
        if not row:
            logger.warning("calendar_response.apply: no invite found for message_id=%s", message_id)
            return

        ical_uid = row["ical_uid"]
        graph = get_graph_client()

        if action == "accept":
            graph.accept_event(ical_uid)
            from models.types import CalendarInvite

            invite = CalendarInvite(
                message_id=row["message_id"],
                graph_message_id=row["graph_message_id"],
                ical_uid=ical_uid,
                title=row["title"],
                start=row["start_time"],
                end=row["end_time"],
                timezone=row["timezone"],
                organizer=row["organizer"],
                zoom_link=row["zoom_link"],
                location=row["location"],
            )
            event_link = gcal.add_event(invite)
            logger.info("Google Calendar event created: %s", event_link)
        elif action == "decline":
            graph.decline_event(ical_uid)
        elif action == "maybe":
            graph.tentatively_accept_event(ical_uid)
        else:
            logger.warning("calendar_response.apply: unknown action=%s", action)
            return

        repo_cal.set_response(conn, message_id, action)
        logger.info("Calendar response applied: message_id=%s action=%s", message_id, action)
