import json
import os

import anthropic

from models.types import Category, Classification

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def classify(system_prompt: str, user_message: str) -> Classification:
    """Call Claude Sonnet with the given prompt and parse the JSON classification response."""
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

    text = response.content[0].text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Claude returned invalid JSON: {e}\n{text}") from e

    if "category" not in data:
        raise ValueError(f"Claude response missing 'category': {data}")

    return Classification(
        category=Category(data["category"]),
        confidence=float(data.get("confidence", 0.0)),
        alternatives=data.get("alternatives", {}),
        tags=data.get("tags", []),
        reasoning=data.get("reasoning", ""),
    )
