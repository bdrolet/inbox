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
  MSAL_SECRET_NAME            — Secret Manager secret for MSAL cache (default: msal-token-cache)
  CLIENT_ID / CLIENT_SECRET / TENANT_ID — Azure app credentials
"""
import base64
import json
import logging
import os

import functions_framework
from cloudevents.http import CloudEvent

from clients.azure.graph_email_client import GraphEmailClient
from clients.bge import load_model
from clients.db import get_conn
from repo import messages, senders
from repo.embeddings import retrieve_neighbors
from services.embedding import embed_and_store, text_for_embedding
from services.ingestion import fetch, normalize

logger = logging.getLogger(__name__)

# Module-level singletons — lazy-initialized on first invocation, reused on warm instances.
# Lazy init keeps container startup fast so Cloud Run health checks pass before model loads.
_graph_client: GraphEmailClient | None = None
_model = None


def _get_graph_client() -> GraphEmailClient:
    global _graph_client
    if _graph_client is None:
        client = GraphEmailClient()
        if not client.authenticate_headless():
            raise RuntimeError("Graph API headless authentication failed")
        _graph_client = client
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

    message_id = notification.get("resourceData", {}).get("id")
    if not message_id:
        logger.warning("Notification missing resourceData.id — skipping")
        return

    graph_client = _get_graph_client()
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

        cleaned = text_for_embedding(msg)
        vec = embed_and_store(conn, msg_id, cleaned, _get_model())
        conn.commit()

        neighbors = retrieve_neighbors(conn, vec, exclude_id=msg_id)

    logger.info(
        f"Stored {msg_id} — {msg['sender']!r}: {msg['subject']!r} "
        f"({len(neighbors)} labeled neighbors)"
    )
