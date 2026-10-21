"""Non-delivery report detection in the inbox pipeline → email_bounced event.

The bounce message is still stored, embedded and classified as usual; this
only adds the event. Best-effort: never raises into the pipeline.
"""

import logging

import clients.otel as otel
from models.message import Message
from services import bounces, email_events
from services.fetching import fetch_attachments

logger = logging.getLogger(__name__)


def maybe_publish(msg: Message, graph_client) -> bool:
    """Publish email_bounced if `msg` is an NDR. True when published."""
    try:
        attachments: list[dict] = []
        if msg.get("has_attachments"):
            try:
                attachments = fetch_attachments(msg["external_id"])
            except Exception:
                logger.warning("Could not fetch attachments for %s", msg["external_id"])
        if not bounces.is_ndr(msg["sender"], msg["subject"], attachments):
            return False
        bounce = bounces.extract(msg, attachments)
        if bounce is None:
            logger.warning("NDR %s could not be parsed — no event", msg["external_id"])
            return False

        original_id = None
        conversation_id = msg.get("conversation_id")
        if conversation_id:
            try:
                original_id = graph_client.sent_message_id_in_conversation(conversation_id)
            except Exception:
                logger.warning("Original-message lookup failed for %s", conversation_id)

        email_events.publish(email_events.build_bounced_event(msg, bounce, original_id))
        otel.emails_bounced_published.add(1)
        return True
    except Exception:
        logger.warning("Bounce handling failed for %s", msg.get("external_id"), exc_info=True)
        return False
