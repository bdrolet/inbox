"""Thin Pub/Sub publisher. I/O only — topic choice and payload shape belong
to the calling service.

The publisher client and topic paths are cached (CF instances are reused
across invocations). OTel trace context is injected as message attributes so
the consumer can continue the trace.
"""

import json
import os

from google.cloud import pubsub_v1
from opentelemetry.propagate import inject

_publisher: pubsub_v1.PublisherClient | None = None
_topic_paths: dict[str, str] = {}


def _client(topic: str) -> tuple[pubsub_v1.PublisherClient, str]:
    global _publisher
    if _publisher is None:
        _publisher = pubsub_v1.PublisherClient()
    if topic not in _topic_paths:
        _topic_paths[topic] = _publisher.topic_path(os.environ["GCP_PROJECT_ID"], topic)
    return _publisher, _topic_paths[topic]


def publish(topic: str, event: dict) -> None:
    """Publish a JSON-encoded event to the named topic with trace context."""
    publisher, path = _client(topic)
    carrier: dict = {}
    inject(carrier)
    publisher.publish(path, json.dumps(event).encode(), **carrier)
