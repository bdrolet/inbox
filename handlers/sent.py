"""Sent Items notification → email_sent event. Nothing is stored, embedded,
classified or tagged (spec §10.3). Duplicates from Graph are acceptable — the
consumer's counters tolerate them."""

import logging
import os
import time

import clients.otel as otel
from clients.graph import get_graph_client
from services import email_events
from services.ingestion import fetch

logger = logging.getLogger(__name__)


def run(notification: dict, context=None) -> None:
    message_id = notification.get("resourceData", {}).get("id")
    if not message_id:
        logger.warning("Sent notification missing resourceData.id — skipping")
        return
    tracer = otel.get_tracer()
    t0 = time.monotonic()
    with tracer.start_as_current_span("inbox.sent", context=context) as span:
        span.set_attribute("message_id", message_id)
        email = fetch(message_id, get_graph_client())
        if email is None:
            logger.warning("Could not fetch sent message %s — skipping", message_id)
            return
        if os.environ.get("GCP_PROJECT_ID") and "[LOCAL-TEST]" in (email.subject or ""):
            logger.info("Skipping local-test sent message %s in GCP", message_id)
            return
        payload = email_events.email_sent_payload(email)
        email_events.publish(payload)
        otel.emails_sent_published.add(1)
        otel.stage_duration.record((time.monotonic() - t0) * 1000, {"stage": "sent"})
        logger.info(
            "Published email_sent %s → %d recipients",
            message_id,
            len(payload["to"]) + len(payload["cc"]),
        )
