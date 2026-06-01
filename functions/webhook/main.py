"""
Cloud Function: inbox webhook receiver.

Handles two Graph API interactions:
  GET  ?validationToken=...  — subscription validation handshake (must reply in 10s)
  POST /                     — change notification; publishes each created message to Pub/Sub

Deploy with:
  gcloud functions deploy inbox-webhook \
    --gen2 --runtime python311 --region us-central1 \
    --source functions/webhook --entry-point webhook \
    --trigger-http --allow-unauthenticated \
    --set-env-vars GCP_PROJECT_ID=bens-project-462804,WEBHOOK_CLIENT_STATE=inbox-webhook
"""
import json
import os

import functions_framework
from google.cloud import pubsub_v1

_publisher: pubsub_v1.PublisherClient | None = None
_messages_topic: str | None = None


def _publisher_client() -> tuple[pubsub_v1.PublisherClient, str]:
    global _publisher, _messages_topic
    if _publisher is None:
        _publisher = pubsub_v1.PublisherClient()
        _messages_topic = _publisher.topic_path(
            os.environ["GCP_PROJECT_ID"], "inbox-messages"
        )
    return _publisher, _messages_topic


@functions_framework.http
def webhook(request):
    # Graph subscription validation handshake — must echo the token as text/plain
    validation_token = request.args.get("validationToken")
    if validation_token:
        return validation_token, 200, {"Content-Type": "text/plain"}

    body = request.get_json(silent=True) or {}
    publisher, topic = _publisher_client()
    client_state = os.environ.get("WEBHOOK_CLIENT_STATE", "inbox-webhook")

    for notification in body.get("value", []):
        # Lifecycle events (subscriptionRemoved, missed, reauthorizationRequired)
        if "lifecycleEvent" in notification:
            continue

        if notification.get("changeType") != "created":
            continue

        if notification.get("clientState") != client_state:
            continue

        publisher.publish(topic, json.dumps(notification).encode())

    return "", 202
