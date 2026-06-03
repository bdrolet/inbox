import json
import logging
import os

import anthropic

from models.types import Category, Classification

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
    logger.debug(
        "Claude usage — in: %d, out: %d, cache_create: %d, cache_read: %d",
        usage.input_tokens,
        usage.output_tokens,
        getattr(usage, "cache_creation_input_tokens", 0) or 0,
        getattr(usage, "cache_read_input_tokens", 0) or 0,
    )

    text = response.content[0].text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        logger.error("Claude returned invalid JSON: %s\nRaw response: %s", e, text)
        raise ValueError(f"Claude returned invalid JSON: {e}\n{text}") from e

    if "category" not in data:
        logger.error("Claude response missing 'category': %s", data)
        raise ValueError(f"Claude response missing 'category': {data}")

    return Classification(
        category=Category(data["category"]),
        confidence=float(data.get("confidence", 0.0)),
        alternatives=data.get("alternatives", {}),
        tags=data.get("tags", []),
        reasoning=data.get("reasoning", ""),
    )
