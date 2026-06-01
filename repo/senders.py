from typing import Optional

import psycopg


def upsert(conn: psycopg.Connection, identifier: str, source: str) -> None:
    conn.execute(
        """
        INSERT INTO senders (identifier, source, first_seen, message_count)
        VALUES (%s, %s, now(), 1)
        ON CONFLICT (source, identifier)
        DO UPDATE SET message_count = senders.message_count + 1
        """,
        (identifier, source),
    )


def get(conn: psycopg.Connection, identifier: str, source: str) -> Optional[dict]:
    return conn.execute(
        "SELECT * FROM senders WHERE source = %s AND identifier = %s",
        (source, identifier),
    ).fetchone()
