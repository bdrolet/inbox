"""
Full message processing pipeline: ingest → embed → classify → store.
"""
import logging

from clients.azure.graph_email_client import GraphEmailClient
from clients.claude import classify
from clients.db import get_conn
from repo import classifications, messages, senders
from repo.embeddings import retrieve_neighbors
from services.classification import PROMPT_VERSION, aggregate_neighbors, build_prompt
from services.embedding import embed_and_store, text_for_embedding
from services.ingestion import fetch, normalize

logger = logging.getLogger(__name__)

_MODEL_NAME = "claude-sonnet-4-6"


def run(notification: dict, graph_client: GraphEmailClient, model) -> None:
    message_id = notification.get("resourceData", {}).get("id")
    if not message_id:
        logger.warning("Notification missing resourceData.id — skipping")
        return

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
        senders.upsert(conn, msg["sender"], msg["source"])
        sender_ctx = senders.get(conn, msg["sender"], msg["source"])

        cleaned = text_for_embedding(msg)
        vec = embed_and_store(conn, msg_id, cleaned, model)
        conn.commit()  # persist message + embedding before LLM call

        neighbors = retrieve_neighbors(conn, vec, exclude_id=msg_id)
        aggregates = aggregate_neighbors(neighbors)
        top_examples = neighbors[:3]

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
        )
        conn.commit()

    logger.info(
        f"Processed {msg_id} — {msg['sender']!r}: {msg['subject']!r} "
        f"→ {result.category.value} ({result.confidence:.2f}) | "
        f"{len(neighbors)} labeled neighbors"
    )
