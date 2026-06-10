"""
Cloud Function entry points: inbox message processor and label handler.

process — triggered by Pub/Sub inbox-messages topic; runs the full classification pipeline.
label   — triggered by Pub/Sub inbox-labels topic; applies human feedback to the vector store.

Required env vars for process:
  GCP_PROJECT_ID              — GCP project (used by Graph auth for Secret Manager)
  CLOUD_SQL_CONNECTION_NAME   — e.g. bens-project-462804:us-central1:inbox
  POSTGRES_USER               — Cloud SQL username
  POSTGRES_PASSWORD           — Cloud SQL password
  POSTGRES_DB                 — database name (default: app)
  ANTHROPIC_API_KEY           — Anthropic API key (injected from Secret Manager)
  MSAL_SECRET_NAME            — Secret Manager secret for MSAL cache (default: msal-token-cache)
  CLIENT_ID / CLIENT_SECRET / TENANT_ID — Azure app credentials
  NTFY_BASE_URL / NTFY_TOPIC / NTFY_TOKEN / WEBHOOK_URL / WEBHOOK_LABEL_TOKEN — ntfy
  GRAFANA_OTLP_ENDPOINT / GRAFANA_OTLP_TOKEN — Grafana Cloud OTLP (optional)

Heavy imports (PyTorch via clients.bge, handlers.pipeline) are deferred inside process()
so the inbox-label CF cold-starts without loading the model (~518 MiB).
"""

import base64
import json
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

import functions_framework
from cloudevents.http import CloudEvent
from opentelemetry.propagate import extract

import clients.otel as otel
from services import labeling

logger = logging.getLogger(__name__)

otel.setup_telemetry("inbox-process")

_model = None


def _get_model():
    global _model
    if _model is None:
        from clients.bge import load_model

        _model = load_model()
    return _model


@functions_framework.cloud_event
def process(cloud_event: CloudEvent) -> None:
    from handlers.pipeline import run as run_pipeline

    data = base64.b64decode(cloud_event.data["message"]["data"]).decode()
    notification = json.loads(data)
    attrs = cloud_event.data["message"].get("attributes", {})
    ctx = extract(attrs)
    # Flush before processing to export a cumulative baseline. Without this,
    # cold-start invocations produce a single OTLP data point (counter=1) and
    # Prometheus increase() requires ≥2 samples to show a non-zero result.
    otel.flush()
    try:
        run_pipeline(notification, _get_model(), context=ctx)
    finally:
        otel.flush()


@functions_framework.cloud_event
def label(cloud_event: CloudEvent) -> None:
    data = base64.b64decode(cloud_event.data["message"]["data"]).decode()
    payload = json.loads(data)
    attrs = cloud_event.data["message"].get("attributes", {})
    ctx = extract(attrs)
    logger.info(
        "Label feedback received: message_id=%s label=%s source=%s",
        payload.get("message_id"),
        payload.get("label"),
        payload.get("source"),
    )
    try:
        labeling.apply_label(
            message_id=payload["message_id"],
            label=payload["label"],
            source=payload["source"],
            context=ctx,
        )
        logger.info("Label applied — message_id=%s", payload["message_id"])
    finally:
        otel.flush()
