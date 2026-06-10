from typing import Optional

import psycopg
from psycopg.types.json import Jsonb


def insert(
    conn: psycopg.Connection,
    message_id: str,
    category: str,
    source: str,  # 'llm' | 'human_correction' | 'human_confirmation'
    confidence: Optional[float] = None,
    alternatives: Optional[dict] = None,
    tags: Optional[list] = None,
    reasoning: Optional[str] = None,
    model: Optional[str] = None,
    prompt_version: Optional[str] = None,
    importance: Optional[str] = None,
) -> str:
    row = conn.execute(
        """
        INSERT INTO classifications
            (message_id, category, importance, confidence, alternatives, tags,
             reasoning, model, prompt_version, source)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            message_id,
            category,
            importance,
            confidence,
            Jsonb(alternatives) if alternatives is not None else None,
            tags,
            reasoning,
            model,
            prompt_version,
            source,
        ),
    ).fetchone()
    return str(row["id"])
