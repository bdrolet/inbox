"""
Cloud Function entry points: inbox message processor, label handler, and search.

process — triggered by Pub/Sub inbox-messages topic; runs the full classification pipeline.
label   — triggered by Pub/Sub inbox-labels topic; applies human feedback to the vector store.
search  — HTTP trigger; searches primary mailbox, shared mailboxes, and M365 groups.

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

Additional env vars for search:
  SEARCH_TOKEN      — Bearer token callers must include in Authorization header
  SHARED_MAILBOXES  — Comma-separated shared mailbox emails to search by default (optional)

Heavy imports (PyTorch via clients.bge, handlers.pipeline) are deferred inside process()
so the inbox-label CF cold-starts without loading the model (~518 MiB).
"""

import base64
import json
import logging
import os

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
def calendar_action(cloud_event: CloudEvent) -> None:
    data = base64.b64decode(cloud_event.data["message"]["data"]).decode()
    payload = json.loads(data)
    message_id = payload.get("message_id")
    action = payload.get("action")
    logger.info("Calendar action received: message_id=%s action=%s", message_id, action)
    otel.flush()
    try:
        from services.calendar_response import apply

        apply(message_id=message_id, action=action)
        logger.info("Calendar action applied — message_id=%s action=%s", message_id, action)
    finally:
        otel.flush()


@functions_framework.http
def search(request):
    """Search primary mailbox, shared mailboxes, and M365 groups via Graph API, or the DB.

    POST /
    Authorization: Bearer {SEARCH_TOKEN}
    Body: {"query": "...", "mode": "graph"|"db", "mailboxes": [...], "limit": 25}
    """
    from datetime import datetime, timezone

    expected_token = os.environ.get("SEARCH_TOKEN")
    if expected_token:
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {expected_token}":
            return "", 401

    body = request.get_json(silent=True) or {}
    query = (body.get("query") or "").strip()
    if not query:
        return json.dumps({"error": "query is required"}), 400, {"Content-Type": "application/json"}

    mode = body.get("mode", "graph")
    limit = min(int(body.get("limit", 25)), 100)

    if mode == "db":
        from clients.db import get_conn
        from repo.messages import search_messages

        conn = get_conn()
        try:
            rows = search_messages(conn, query, limit=limit)
        finally:
            conn.close()

        results = []
        for r in rows:
            received_at = r.get("received_at")
            results.append({
                "subject": r.get("subject"),
                "sender": r.get("sender"),
                "sender_display": r.get("sender_display"),
                "received_at": received_at.isoformat() if isinstance(received_at, datetime) else str(received_at) if received_at else None,
                "mailbox": "db",
                "web_link": None,
                "category": r.get("category"),
                "importance": r.get("importance"),
            })
        return json.dumps({"results": results}), 200, {"Content-Type": "application/json"}

    # Graph mode
    from clients.azure import GraphEmailClient

    client = GraphEmailClient()
    if not client.authenticate_headless():
        logger.error("search: Graph authentication failed")
        return json.dumps({"error": "authentication failed"}), 503, {"Content-Type": "application/json"}

    if "mailboxes" in body:
        mailboxes = body["mailboxes"]
    else:
        shared = [m.strip() for m in os.environ.get("SHARED_MAILBOXES", "").split(",") if m.strip()]
        mailboxes = ["me"] + shared

    collected: list[tuple] = []  # (Email, source_label)

    for mailbox in mailboxes:
        for email in client.search_emails(query, mailbox=mailbox, limit=limit):
            collected.append((email, mailbox))

    for group in client.get_member_groups():
        label = f"group:{group['mail'] or group['id']}"
        for email in client.search_group_conversations(group["id"], query, limit=limit):
            collected.append((email, label))

    # Deduplicate on (subject, sender_email, minute-truncated received_at)
    seen: set = set()
    unique: list[tuple] = []
    for email, source in collected:
        received = email.received_datetime
        if isinstance(received, datetime):
            bucket = received.replace(second=0, microsecond=0).isoformat()
        else:
            bucket = str(received)
        key = (email.subject, email.from_email, bucket)
        if key not in seen:
            seen.add(key)
            unique.append((email, source))

    unique.sort(key=lambda item: item[0].received_datetime if isinstance(item[0].received_datetime, datetime) else datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    unique = unique[:limit]

    results = []
    for email, source in unique:
        received = email.received_datetime
        results.append({
            "subject": email.subject,
            "sender": email.from_email,
            "sender_display": email.from_name,
            "received_at": received.isoformat() if isinstance(received, datetime) else str(received) if received else None,
            "preview": email.body_preview,
            "mailbox": source,
            "web_link": email.web_link,
            "category": None,
            "importance": None,
        })

    return json.dumps({"results": results}), 200, {"Content-Type": "application/json"}


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
    otel.flush()
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
