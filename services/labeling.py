import logging
from typing import Optional

import clients.otel as otel
from clients.db import get_conn
from repo import classifications
from repo.embeddings import set_current_importance, set_current_label
from services import email_events

logger = logging.getLogger(__name__)


def apply_label(
    message_id: str,
    label: str,
    source: str,
    importance: Optional[str] = None,
    context=None,
) -> None:
    """
    Record a human label (confirmation or correction) for a message.
    source must be 'human_confirmation' or 'human_correction'.
    Sets current_label (and optionally current_importance) on the embedding
    so it becomes eligible for retrieval context.
    """
    logger.info(
        "Applying label %r (importance=%s) to %s (source=%s)",
        label,
        importance,
        message_id,
        source,
    )
    with get_conn() as conn:
        classifications.insert(conn, message_id=message_id, category=label, source=source)
        set_current_label(conn, message_id, label)
        if importance is not None:
            set_current_importance(conn, message_id, importance)
        conn.commit()
    logger.debug("Label %r applied and embedding updated for %s", label, message_id)
    otel.human_feedback.add(1, {"source": source, "category": label})
    try:
        email_events.publish(
            {
                "event": "label_applied",
                "message_id": message_id,
                "task_gid": None,  # inbox doesn't track task GIDs; tasks resolves via DB / external:{message_id}
                "label": label,
                "source": source,
            }
        )
    except Exception:
        logger.exception("label_applied publish failed for message_id=%s", message_id)
