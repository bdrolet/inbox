import psycopg


def store(conn: psycopg.Connection, message_id: str, vec: list[float]) -> None:
    conn.execute(
        """
        INSERT INTO message_embeddings (message_id, embedding)
        VALUES (%s, %s)
        ON CONFLICT (message_id) DO UPDATE
            SET embedding = EXCLUDED.embedding,
                updated_at = now()
        """,
        (message_id, vec),
    )


def retrieve_neighbors(
    conn: psycopg.Connection,
    vec: list[float],
    exclude_id: str,
    k: int = 10,
) -> list[dict]:
    return conn.execute(
        """
        SELECT m.subject, m.body, m.sender,
               me.current_label,
               me.current_importance,
               1 - (me.embedding <=> %s) AS similarity
        FROM message_embeddings me
        JOIN messages m ON m.id = me.message_id
        WHERE me.current_label IS NOT NULL
          AND me.message_id != %s
        ORDER BY me.embedding <=> %s
        LIMIT %s
        """,
        (vec, exclude_id, vec, k),
    ).fetchall()


def set_current_label(conn: psycopg.Connection, message_id: str, label: str) -> None:
    conn.execute(
        """
        UPDATE message_embeddings
           SET current_label = %s, updated_at = now()
         WHERE message_id = %s
        """,
        (label, message_id),
    )


def set_current_importance(conn: psycopg.Connection, message_id: str, importance: str) -> None:
    conn.execute(
        """
        UPDATE message_embeddings
           SET current_importance = %s, updated_at = now()
         WHERE message_id = %s
        """,
        (importance, message_id),
    )
