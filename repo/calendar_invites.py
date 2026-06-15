from models.types import CalendarInvite


def insert(conn, invite: CalendarInvite) -> str:
    row = conn.execute(
        """
        INSERT INTO calendar_invites
            (message_id, graph_message_id, ical_uid, title, start_time, end_time,
             timezone, organizer, zoom_link, location)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            invite.message_id,
            invite.graph_message_id,
            invite.ical_uid,
            invite.title,
            invite.start,
            invite.end,
            invite.timezone,
            invite.organizer,
            invite.zoom_link,
            invite.location,
        ),
    ).fetchone()
    return str(row["id"])


def get_by_message_id(conn, message_id: str) -> dict | None:
    return conn.execute(
        "SELECT * FROM calendar_invites WHERE message_id = %s ORDER BY created_at DESC LIMIT 1",
        (message_id,),
    ).fetchone()


def set_response(conn, message_id: str, response: str) -> None:
    conn.execute(
        """
        UPDATE calendar_invites
        SET user_response = %s, responded_at = NOW()
        WHERE message_id = %s
        """,
        (response, message_id),
    )
