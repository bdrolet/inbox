import logging

import repo.calendar_invites as repo_cal
from clients.db import get_conn
from clients.graph import get_graph_client

logger = logging.getLogger(__name__)


def apply(message_id: str, action: str) -> None:
    """
    Respond to a calendar invite action (accept | decline | maybe).

    - Sends the RSVP via Graph API
    - Updates user_response in DB

    Google Calendar events are owned by the schedule service, which creates them
    at email-classification time — inbox must not create them here.
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
        elif action == "decline":
            graph.decline_event(ical_uid)
        elif action == "maybe":
            graph.tentatively_accept_event(ical_uid)
        else:
            logger.warning("calendar_response.apply: unknown action=%s", action)
            return

        repo_cal.set_response(conn, message_id, action)
        logger.info("Calendar response applied: message_id=%s action=%s", message_id, action)
