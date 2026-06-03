from clients.db import get_conn
from repo import classifications
from repo.embeddings import set_current_label


def apply_label(message_id: str, label: str, source: str) -> None:
    """
    Record a human label (confirmation or correction) for a message.
    source must be 'human_confirmation' or 'human_correction'.
    Sets current_label on the embedding so it becomes eligible for retrieval context.
    """
    with get_conn() as conn:
        classifications.insert(conn, message_id=message_id, category=label, source=source)
        set_current_label(conn, message_id, label)
        conn.commit()
