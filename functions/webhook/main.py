"""
Cloud Function: inbox webhook receiver.

Handles three interactions:
  GET  ?validationToken=...  — subscription validation handshake (must reply in 10s)
  POST /                     — change notification; publishes each created message to Pub/Sub
  POST /label                — human feedback from ntfy action buttons; publishes to inbox-labels
  GET  /label?...&token=...  — human feedback from Asana action links (browser click); same effect

Deploy with:
  gcloud functions deploy inbox-webhook \
    --gen2 --runtime python311 --region us-central1 \
    --source functions/webhook --entry-point webhook \
    --trigger-http --allow-unauthenticated \
    --set-env-vars GCP_PROJECT_ID=bens-project-462804,WEBHOOK_CLIENT_STATE=inbox-webhook
"""
import json
import logging
import os

import functions_framework
from google.cloud import pubsub_v1
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.propagate import inject
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

logger = logging.getLogger(__name__)

_publisher: pubsub_v1.PublisherClient | None = None
_messages_topic: str | None = None
_labels_topic: str | None = None
_tracer_provider: TracerProvider | None = None


def _setup_telemetry() -> None:
    global _tracer_provider
    endpoint = os.environ.get("GRAFANA_OTLP_ENDPOINT")
    if not endpoint or _tracer_provider is not None:
        return
    token = os.environ.get("GRAFANA_OTLP_TOKEN", "")
    _tracer_provider = TracerProvider(resource=Resource({"service.name": "inbox-webhook"}))
    _tracer_provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(
                endpoint=f"{endpoint}/v1/traces",
                headers={"Authorization": f"Basic {token}"},
            )
        )
    )
    trace.set_tracer_provider(_tracer_provider)


def _flush() -> None:
    if _tracer_provider is not None:
        _tracer_provider.force_flush(timeout_millis=30_000)


def _get_tracer() -> trace.Tracer:
    return trace.get_tracer("inbox-webhook")


def _publisher_client() -> tuple[pubsub_v1.PublisherClient, str, str]:
    global _publisher, _messages_topic, _labels_topic
    if _publisher is None:
        _publisher = pubsub_v1.PublisherClient()
        project = os.environ["GCP_PROJECT_ID"]
        _messages_topic = _publisher.topic_path(project, "inbox-messages")
        _labels_topic   = _publisher.topic_path(project, "inbox-labels")
    return _publisher, _messages_topic, _labels_topic


_setup_telemetry()


@functions_framework.http
def webhook(request):
    # Graph subscription validation handshake — must echo the token as text/plain
    validation_token = request.args.get("validationToken")
    if validation_token:
        logger.info("Graph subscription validation handshake")
        return validation_token, 200, {"Content-Type": "text/plain"}

    publisher, messages_topic, labels_topic = _publisher_client()

    try:
        # Human feedback from ntfy action buttons or Asana task action links
        if request.path == "/label":
            expected = os.environ.get("WEBHOOK_LABEL_TOKEN")
            if expected:
                auth  = request.headers.get("Authorization", "")
                token = request.args.get("token", "")
                if auth != f"Bearer {expected}" and token != expected:
                    logger.warning("Rejected /label request — invalid auth")
                    return "", 403
            message_id = request.args.get("id")
            label      = request.args.get("label")
            source     = request.args.get("source", "human_correction")
            logger.info("Label callback: id=%s label=%s source=%s", message_id, label, source)

            with _get_tracer().start_as_current_span("inbox.webhook.label") as span:
                span.set_attribute("message_id", message_id or "")
                span.set_attribute("label", label or "")
                span.set_attribute("source", source)
                carrier = {}
                inject(carrier)
                publisher.publish(
                    labels_topic,
                    json.dumps({"message_id": message_id, "label": label, "source": source}).encode(),
                    **carrier,
                )
            if request.method == "GET":
                return f"Label '{label}' applied.", 200, {"Content-Type": "text/plain"}
            return "", 202

        # Graph change notifications
        body = request.get_json(silent=True) or {}
        client_state = os.environ.get("WEBHOOK_CLIENT_STATE", "inbox-webhook")
        published = 0

        with _get_tracer().start_as_current_span("inbox.webhook") as span:
            for notification in body.get("value", []):
                if "lifecycleEvent" in notification:
                    logger.warning("Lifecycle event: %s", notification.get("lifecycleEvent"))
                    continue

                if notification.get("changeType") != "created":
                    continue

                if notification.get("clientState") != client_state:
                    logger.warning("Unexpected clientState: %s", notification.get("clientState"))
                    continue

                carrier = {}
                inject(carrier)
                publisher.publish(messages_topic, json.dumps(notification).encode(), **carrier)
                published += 1

            span.set_attribute("published_count", published)

        logger.info("Published %d notification(s)", published)
        return "", 202

    finally:
        _flush()
