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
  NTFY_BASE_URL / NTFY_TOPIC / NTFY_TOKEN / WEBHOOK_URL — ntfy notifications
"""
import base64
import json
import logging

import functions_framework
from cloudevents.http import CloudEvent

from clients.bge import load_model
from handlers.pipeline import run as run_pipeline
from services import labeling

logger = logging.getLogger(__name__)

_model = None


def _get_model():
    global _model
    if _model is None:
        _model = load_model()
    return _model


@functions_framework.cloud_event
def process(cloud_event: CloudEvent) -> None:
    data = base64.b64decode(cloud_event.data["message"]["data"]).decode()
    notification = json.loads(data)
    run_pipeline(notification, _get_model())


@functions_framework.cloud_event
def label(cloud_event: CloudEvent) -> None:
    data = base64.b64decode(cloud_event.data["message"]["data"]).decode()
    payload = json.loads(data)
    logger.info(
        "Label feedback received: message_id=%s label=%s source=%s",
        payload.get("message_id"), payload.get("label"), payload.get("source"),
    )
    labeling.apply_label(
        message_id=payload["message_id"],
        label=payload["label"],
        source=payload["source"],
    )
    logger.info("Label applied — message_id=%s", payload["message_id"])
