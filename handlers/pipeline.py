"""
Full message processing pipeline: ingest → embed → classify → store → dispatch.
"""
import logging

from clients.graph import get_graph_client
from clients.claude import classify
from clients.db import get_conn
from repo import classifications, messages, senders
from repo.embeddings import retrieve_neighbors
from services.classification import PROMPT_VERSION, aggregate_neighbors, build_prompt
from services.embedding import embed_and_store, text_for_embedding
from services.ingestion import fetch, normalize
from handlers.actions.dispatch import dispatch

logger = logging.getLogger(__name__)

_MODEL_NAME = "claude-sonnet-4-6"


def run(notification: dict, model) -> None:
    message_id = notification.get("resourceData", {}).get("id")
    if not message_id:
        logger.warning("Notification missing resourceData.id — skipping")
        return

    graph_client = get_graph_client()

    email = fetch(message_id, graph_client)
    if email is None:
        logger.warning(f"Could not fetch email {message_id} — skipping")
        return

    msg = normalize(email, raw=notification)

    with get_conn() as conn:
        if messages.exists(conn, msg["source"], msg["external_id"]):
            logger.debug(f"Duplicate {msg['external_id']} — skipping")
            return

        msg_id = messages.insert(conn, msg)
        msg["id"] = msg_id  # make DB UUID available to action handlers (ntfy action buttons)
        senders.upsert(conn, msg["sender"], msg["source"])
        sender_ctx = senders.get(conn, msg["sender"], msg["source"])

        cleaned = text_for_embedding(msg)
        vec = embed_and_store(conn, msg_id, cleaned, model)
        conn.commit()  # persist message + embedding before LLM call

        neighbors = retrieve_neighbors(conn, vec, exclude_id=msg_id)
        aggregates = aggregate_neighbors(neighbors)
        top_examples = neighbors[:3]

        logger.debug(
            "Classifying %s — %d labeled neighbors, aggregates: %s",
            msg_id, len(neighbors), aggregates,
        )
        system_prompt, user_message = build_prompt(msg, aggregates, top_examples, sender_ctx)
        result = classify(system_prompt, user_message)

        classifications.insert(
            conn,
            message_id=msg_id,
            category=result.category.value,
            source="llm",
            confidence=result.confidence,
            alternatives=result.alternatives,
            tags=result.tags,
            reasoning=result.reasoning,
            model=_MODEL_NAME,
            prompt_version=PROMPT_VERSION,
            importance=result.importance.value,
        )
        conn.commit()

    dispatch(result, msg)

    logger.info(
        "Processed %s — %r: %r → %s (%s, %.2f) | %d labeled neighbors",
        msg_id, msg["sender"], msg["subject"],
        result.category.value, result.importance.value, result.confidence,
        len(neighbors),
    )
