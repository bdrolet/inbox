"""Re-evaluate a stale urgent message: gather evidence (content, age, reply
signal) and ask Claude for a verdict. One concern: producing a verdict string.

Fail safe: any failure returns "still_urgent" — the verdict that changes
nothing. Verdicts are time-based policy, not human feedback; they never touch
current_label or emit label_applied events.
"""

import logging
from datetime import datetime
from typing import Any

from clients.claude import retriage_verdict

logger = logging.getLogger(__name__)

_BODY_LIMIT = 2000

VERDICTS = {"still_urgent", "needs_response", "resolved_or_expired"}

SYSTEM_PROMPT = """You are re-evaluating an email that was classified URGENT when it \
arrived but has sat in the owner's inbox for several days. Decide its current \
disposition.

Respond with JSON only: {"verdict": "<verdict>", "reason": "<one line>"}

Verdicts:
- "still_urgent": still time-sensitive and actionable; the owner has not dealt with it.
- "needs_response": no longer on fire, but still deserves a reply from the owner.
- "resolved_or_expired": the owner already replied, the deadline or event has passed, \
or no action is required anymore.

The owner-reply excerpt is factual (their latest Sent Items message in the thread). \
Read it for meaning: a holding reply ("on it", "will do this Friday") means the matter \
is STILL PENDING, not resolved; only a reply that actually resolves the matter supports \
"resolved_or_expired". Be conservative: when uncertain, answer "still_urgent"."""


def evaluate(
    client: Any,
    message_id: str,
    conversation_id: str | None,
    received_at: datetime,
    now: datetime,
) -> str:
    """Verdict for a stale urgent message. Always returns a member of VERDICTS."""
    try:
        email = client.get_email_details(message_id)
        if email is None:
            logger.warning("retriage: could not fetch %s — keeping urgent", message_id)
            return "still_urgent"

        reply = None
        if conversation_id:
            reply = client.latest_reply_from_me(conversation_id, received_at)
        reply_line = (
            f'Owner\'s latest reply in this thread since it arrived: "{reply[:300]}"'
            if reply
            else "Owner has not replied in this thread since it arrived."
        )

        user_message = (
            f"Subject: {email.subject}\n"
            f"From: {email.from_display}\n"
            f"Received: {email.received_date} ({(now - received_at).days} days ago)\n"
            f"Today: {now.date().isoformat()}\n"
            f"{reply_line}\n\n"
            f"Body:\n{email.get_body_text()[:_BODY_LIMIT]}"
        )
        data = retriage_verdict(SYSTEM_PROMPT, user_message)
        verdict = data.get("verdict", "")
        logger.info("retriage %s -> %s (%s)", message_id, verdict, data.get("reason", ""))
        if verdict not in VERDICTS:
            return "still_urgent"
        return verdict
    except Exception:
        logger.warning("retriage failed for %s — keeping urgent", message_id, exc_info=True)
        return "still_urgent"
