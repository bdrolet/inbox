import json
import logging
import os

import anthropic

import clients.otel as otel
from models.types import Category, Classification, Importance

logger = logging.getLogger(__name__)

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def classify(system_prompt: str, user_message: str) -> Classification:
    """Call Claude Sonnet with the given prompt and parse the JSON classification response."""
    logger.debug("Calling Claude for classification (user_message=%d chars)", len(user_message))

    response = _get_client().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        temperature=0.2,
        system=[
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_message}],
    )

    usage = response.usage
    cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    logger.debug(
        "Claude usage — in: %d, out: %d, cache_create: %d, cache_read: %d",
        usage.input_tokens, usage.output_tokens, cache_creation, cache_read,
    )
    otel.claude_tokens.add(usage.input_tokens, {"token_type": "input"})
    otel.claude_tokens.add(usage.output_tokens, {"token_type": "output"})
    otel.claude_tokens.add(cache_read, {"token_type": "cache_read"})
    otel.claude_tokens.add(cache_creation, {"token_type": "cache_creation"})

    text = response.content[0].text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        logger.error("Claude returned invalid JSON: %s\nRaw response: %s", e, text)
        raise ValueError(f"Claude returned invalid JSON: {e}\n{text}") from e

    if "category" not in data:
        logger.error("Claude response missing 'category': %s", data)
        raise ValueError(f"Claude response missing 'category': {data}")

    raw_importance = data.get("importance", "P2")
    try:
        importance = Importance(raw_importance)
    except ValueError:
        logger.warning("Unrecognized importance value %r — defaulting to P2", raw_importance)
        importance = Importance.P2

    return Classification(
        category=Category(data["category"]),
        confidence=float(data.get("confidence", 0.0)),
        alternatives=data.get("alternatives", {}),
        tags=data.get("tags", []),
        reasoning=data.get("reasoning", ""),
        importance=importance,
    )


def extract(prompt: str) -> str:
    """Single-turn extraction call. Temperature 0, max_tokens 20. Returns raw stripped text."""
    response = _get_client().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=20,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    usage = response.usage
    otel.claude_tokens.add(usage.input_tokens, {"token_type": "input"})
    otel.claude_tokens.add(usage.output_tokens, {"token_type": "output"})
    return response.content[0].text.strip()
