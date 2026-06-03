import logging

from clients.db import get_conn
from repo import classifications
from repo.embeddings import set_current_label

logger = logging.getLogger(__name__)


def apply_label(message_id: str, label: str, source: str) -> None:
    """
    Record a human label (confirmation or correction) for a message.
    source must be 'human_confirmation' or 'human_correction'.
    Sets current_label on the embedding so it becomes eligible for retrieval context.
    """
    logger.info("Applying label %r to %s (source=%s)", label, message_id, source)
    with get_conn() as conn:
        classifications.insert(conn, message_id=message_id, category=label, source=source)
        set_current_label(conn, message_id, label)
        conn.commit()
    logger.debug("Label %r applied and embedding updated for %s", label, message_id)
