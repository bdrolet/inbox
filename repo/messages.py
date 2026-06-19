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


def search_messages(conn: psycopg.Connection, query: str, limit: int = 25) -> list[dict]:
    """Full-text search on stored messages, returning latest classification per result."""
    pattern = f"%{query}%"
    rows = conn.execute(
        """
        SELECT
            m.id,
            m.subject,
            m.sender,
            m.sender_display,
            m.received_at,
            c.category,
            c.importance
        FROM messages m
        LEFT JOIN LATERAL (
            SELECT category, importance
            FROM classifications
            WHERE message_id = m.id
            ORDER BY created_at DESC
            LIMIT 1
        ) c ON true
        WHERE
            m.subject        ILIKE %s
            OR m.sender_display ILIKE %s
            OR m.body           ILIKE %s
        ORDER BY m.received_at DESC
        LIMIT %s
        """,
        (pattern, pattern, pattern, limit),
    ).fetchall()
    return [dict(r) for r in rows]
