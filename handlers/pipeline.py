"""
Full message processing pipeline: ingest → embed → classify → store → dispatch.
"""

import logging
import os
import time

from opentelemetry.trace import StatusCode

import clients.hubspot as hubspot
import clients.otel as otel
from clients.claude import classify
from clients.db import get_conn
from clients.graph import get_graph_client
from handlers.actions.dispatch import dispatch
from repo import classifications, messages, senders
from repo.embeddings import retrieve_neighbors
from services import asana_tag_cache as tag_cache_svc
from services.classification import PROMPT_VERSION, aggregate_neighbors, build_prompt
from services.embedding import embed_and_store, text_for_embedding
from services.ingestion import fetch, normalize

logger = logging.getLogger(__name__)

_MODEL_NAME = "claude-sonnet-4-6"


def run(notification: dict, model, context=None) -> None:
    message_id = notification.get("resourceData", {}).get("id")
    if not message_id:
        logger.warning("Notification missing resourceData.id — skipping")
        return

    tracer = otel.get_tracer()
    pipeline_start = time.monotonic()

    with tracer.start_as_current_span("inbox.process", context=context) as root_span:
        try:
            graph_client = get_graph_client()

            # Fetch
            t0 = time.monotonic()
            with tracer.start_as_current_span("inbox.fetch") as span:
                span.set_attribute("message_id", message_id)
                email = fetch(message_id, graph_client)
            otel.stage_duration.record((time.monotonic() - t0) * 1000, {"stage": "fetch"})

            if email is None:
                logger.warning(f"Could not fetch email {message_id} — skipping")
                return

            if os.environ.get("GCP_PROJECT_ID") and "[LOCAL-TEST]" in (email.subject or ""):
                logger.info("Skipping local-test email %s in GCP", message_id)
                return

            msg = normalize(email, raw=notification)

            with get_conn() as conn:
                if messages.exists(conn, msg["source"], msg["external_id"]):
                    logger.debug(f"Duplicate {msg['external_id']} — skipping")
                    otel.emails_duplicates.add(1)
                    return

                msg_id = messages.insert(conn, msg)
                msg["id"] = (
                    msg_id  # make DB UUID available to action handlers (ntfy action buttons)
                )
                senders.upsert(conn, msg["sender"], msg["source"])
                sender_ctx = senders.get(conn, msg["sender"], msg["source"])

                # Embed
                t0 = time.monotonic()
                with tracer.start_as_current_span("inbox.embed") as span:
                    cleaned = text_for_embedding(msg)
                    span.set_attribute("text_length", len(cleaned))
                    vec = embed_and_store(conn, msg_id, cleaned, model)
                otel.stage_duration.record((time.monotonic() - t0) * 1000, {"stage": "embed"})

                conn.commit()  # persist message + embedding before LLM call

                # Retrieve neighbors
                t0 = time.monotonic()
                with tracer.start_as_current_span("inbox.retrieve_neighbors") as span:
                    neighbors = retrieve_neighbors(conn, vec, exclude_id=msg_id)
                    span.set_attribute("neighbor_count", len(neighbors))
                    labeled = [n for n in neighbors if n.get("current_label")]
                    span.set_attribute("labeled_count", len(labeled))
                otel.stage_duration.record(
                    (time.monotonic() - t0) * 1000, {"stage": "retrieve_neighbors"}
                )

                aggregates = aggregate_neighbors(neighbors)
                top_examples = neighbors[:3]

                logger.debug(
                    "Classifying %s — %d labeled neighbors, aggregates: %s",
                    msg_id,
                    len(neighbors),
                    aggregates,
                )

                # Classify
                t0 = time.monotonic()
                with tracer.start_as_current_span("inbox.classify") as span:
                    system_prompt, user_message = build_prompt(
                        msg, aggregates, top_examples, sender_ctx
                    )
                    classification = classify(system_prompt, user_message)
                    span.set_attribute("category", classification.category.value)
                    span.set_attribute("importance", classification.importance.value)
                    span.set_attribute("confidence", classification.confidence)
                    span.set_attribute("model", _MODEL_NAME)
                otel.stage_duration.record((time.monotonic() - t0) * 1000, {"stage": "classify"})

                try:
                    classification.tag_gids = tag_cache_svc.resolve_gids(classification.tags)
                except Exception:
                    logger.exception("Tag GID resolution failed for message_id=%s", msg_id)

                classifications.insert(
                    conn,
                    message_id=msg_id,
                    category=classification.category.value,
                    source="llm",
                    confidence=classification.confidence,
                    alternatives=classification.alternatives,
                    tags=classification.tags,
                    reasoning=classification.reasoning,
                    model=_MODEL_NAME,
                    prompt_version=PROMPT_VERSION,
                    importance=classification.importance.value,
                )
                conn.commit()

            # Dispatch
            t0 = time.monotonic()
            with tracer.start_as_current_span("inbox.dispatch") as span:
                span.set_attribute("category", classification.category.value)
                dispatch(classification, msg)
            otel.stage_duration.record((time.monotonic() - t0) * 1000, {"stage": "dispatch"})

            try:
                contact_id = hubspot.upsert_contact(msg["sender"], msg["sender_display"])
                if contact_id:
                    hubspot.log_email(
                        contact_id,
                        msg["subject"],
                        msg["sender"],
                        msg["body"],
                        msg["received_at"],
                        body_html=msg.get("body_html"),
                    )
            except Exception:
                logger.warning("HubSpot logging failed", exc_info=True)

            total_ms = (time.monotonic() - pipeline_start) * 1000
            otel.stage_duration.record(total_ms, {"stage": "total"})
            otel.emails_processed.add(
                1,
                {
                    "category": classification.category.value,
                    "importance": classification.importance.value,
                },
            )
            otel.confidence_hist.record(
                classification.confidence, {"category": classification.category.value}
            )
            otel.neighbors_hist.record(len(neighbors))

            logger.info(
                "Processed %s — %r: %r → %s (%s, %.2f) | %d labeled neighbors",
                msg_id,
                msg["sender"],
                msg["subject"],
                classification.category.value,
                classification.importance.value,
                classification.confidence,
                len(neighbors),
            )

        except Exception as e:
            root_span.set_status(StatusCode.ERROR)
            root_span.record_exception(e)
            otel.pipeline_errors.add(1, {"stage": "pipeline"})
            raise
