from typing import Optional

import psycopg
from psycopg.types.json import Jsonb

from models.message import Message


def insert(conn: psycopg.Connection, msg: Message) -> str:
    row = conn.execute(
        """
        INSERT INTO messages
            (source, external_id, sender, sender_display, subject, body,
             received_at, thread_id, raw)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            msg["source"],
            msg["external_id"],
            msg["sender"],
            msg["sender_display"],
            msg["subject"],
            msg["body"],
            msg["received_at"],
            msg["thread_id"],
            Jsonb(msg["raw"]),
        ),
    ).fetchone()
    return str(row["id"])


def exists(conn: psycopg.Connection, source: str, external_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM messages WHERE source = %s AND external_id = %s",
        (source, external_id),
    ).fetchone()
    return row is not None


def get(conn: psycopg.Connection, message_id: str) -> Optional[dict]:
    return conn.execute(
        "SELECT * FROM messages WHERE id = %s",
        (message_id,),
    ).fetchone()
