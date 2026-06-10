from datetime import datetime

from models.message import Message

PROMPT_VERSION = "v2"

_SYSTEM_PROMPT = """\
You are an email triage assistant. Classify the given email by assigning a category, an importance level, and relevant tags.

Categories (when to act — purely about timing):
- urgent: needs attention today or before a specific deadline; time-sensitive regardless of stakes
- respond: needs a reply but no hard deadline; moved to "To Respond" folder
- review: worth reading but no reply needed; moved to "To Review" folder
- reference: keep for future reference, no action needed; archived
- ignore: marketing, automated notifications, or noise; archived

Importance (stakes — how much this matters, independent of timing):
- P0: critical — major consequence if missed; affects health, finances, legal standing, or key relationships
- P1: needs to be done — real obligation or meaningful opportunity; will matter if ignored
- P2: would be pretty great if accomplished — worthwhile but not essential; low cost if skipped
- P3: nice to have — minor, low-stakes, or purely informational

Category and importance are independent axes. Examples:
- Failed payment notification → category: ignore (automated), importance: P1 (financial consequence)
- Legal document to review → category: review (read carefully, no reply), importance: P0 (critical)
- Recruiter cold email → category: ignore, importance: P3
- Time-sensitive calendar invite from a colleague → category: urgent, importance: P2

Tag vocabulary (apply all that fit; omit tags that don't apply):
  topic:finances, topic:work, topic:personal, topic:health, topic:legal, topic:travel
  from:family, from:colleague, from:recruiter, from:vendor, from:automated
  action:deadline, action:decision, action:approval, action:meeting

Respond with valid JSON only — no markdown fences, no extra text:
{
  "category": "urgent|respond|review|reference|ignore",
  "importance": "P0|P1|P2|P3",
  "confidence": 0.85,
  "alternatives": {"urgent": 0.05, "respond": 0.85, "review": 0.08, "reference": 0.01, "ignore": 0.01},
  "tags": ["topic:work", "action:decision"],
  "reasoning": "one sentence explaining the classification"
}"""


def aggregate_neighbors(neighbors: list[dict]) -> dict[str, int]:
    """Count neighbors by label."""
    counts: dict[str, int] = {}
    for n in neighbors:
        label = n.get("current_label") or ""
        if label:
            counts[label] = counts.get(label, 0) + 1
    return counts


def build_prompt(
    msg: Message,
    aggregates: dict[str, int],
    top_examples: list[dict],
    sender_ctx: dict | None,
) -> tuple[str, str]:
    """
    Returns (system_prompt, user_message).
    The system prompt is static and cache-eligible.
    The user message contains sender context, retrieval examples, and the current email.
    """
    parts: list[str] = []

    # Sender context
    if sender_ctx:
        total = sender_ctx.get("message_count") or 0
        responded = sender_ctx.get("my_response_count") or 0
        history = f"{responded}/{total} replied" if total > 0 else "no history"
        parts.append(f"Sender history: {history}")
        if sender_ctx.get("relationship_label"):
            parts.append(f"Relationship: {sender_ctx['relationship_label']}")
        if sender_ctx.get("notes"):
            parts.append(f"Notes: {sender_ctx['notes']}")

    # Retrieval context — only include if there are labeled examples
    if top_examples:
        parts.append("")
        parts.append("Similar labeled emails (human-confirmed):")
        for ex in top_examples:
            snippet = (ex.get("body") or "")[:200].strip()
            label = ex["current_label"]
            importance = ex.get("current_importance")
            label_str = f"{label}, {importance}" if importance else label
            parts.append(
                f"  [{label_str}] From: {ex['sender']} | Subject: {ex['subject']}\n  {snippet}"
            )
        if aggregates:
            summary = ", ".join(f"{k}: {v}" for k, v in sorted(aggregates.items()))
            parts.append(f"Label distribution ({sum(aggregates.values())} neighbors): {summary}")

    # Current message
    parts.append("")
    received_at = msg["received_at"]
    if isinstance(received_at, datetime):
        received_str = received_at.strftime("%Y-%m-%d %H:%M UTC")
    else:
        received_str = str(received_at)

    parts.append("Email to classify:")
    parts.append(f"From: {msg['sender_display']} <{msg['sender']}>")
    parts.append(f"Subject: {msg['subject']}")
    parts.append(f"Received: {received_str}")
    parts.append("")
    parts.append(msg["body"][:1500])

    return _SYSTEM_PROMPT, "\n".join(parts)
