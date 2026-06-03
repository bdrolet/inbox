"""
Cloud Function entry point: inbox message processor.

Triggered by Pub/Sub messages on the inbox-messages topic. Each message is a
Graph change notification published by the inbox-webhook Cloud Function.

Required env vars:
  GCP_PROJECT_ID              — GCP project (used by Graph auth for Secret Manager)
  CLOUD_SQL_CONNECTION_NAME   — e.g. bens-project-462804:us-central1:inbox
  POSTGRES_USER               — Cloud SQL username
  POSTGRES_PASSWORD           — Cloud SQL password
  POSTGRES_DB                 — database name (default: app)
  ANTHROPIC_API_KEY           — Anthropic API key (injected from Secret Manager)
  MSAL_SECRET_NAME            — Secret Manager secret for MSAL cache (default: msal-token-cache)
  CLIENT_ID / CLIENT_SECRET / TENANT_ID — Azure app credentials
"""
import base64
import json
import logging

import functions_framework
from cloudevents.http import CloudEvent

from clients.azure.graph_email_client import GraphEmailClient
from clients.bge import load_model
from handlers.pipeline import run as run_pipeline

logger = logging.getLogger(__name__)

# Module-level singletons — lazy-initialized on first invocation, reused on warm instances.
_graph_client: GraphEmailClient | None = None
_model = None


def _get_graph_client() -> GraphEmailClient:
    global _graph_client
    if _graph_client is None:
        _graph_client = GraphEmailClient()
    if not _graph_client.authenticate_headless():
        _graph_client = None
        raise RuntimeError("Graph API headless authentication failed")
    return _graph_client


def _get_model():
    global _model
    if _model is None:
        _model = load_model()
    return _model


@functions_framework.cloud_event
def process(cloud_event: CloudEvent) -> None:
    data = base64.b64decode(cloud_event.data["message"]["data"]).decode()
    notification = json.loads(data)
    run_pipeline(notification, _get_graph_client(), _get_model())
