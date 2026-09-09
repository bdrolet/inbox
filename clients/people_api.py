"""Client for the people-api Cloud Run service (github.com/bdrolet/people).
Sender context for the classification prompt: message_count,
my_response_count, relationship_label, notes. Fail-open — any problem
returns None and the pipeline classifies without sender context. Hot path,
so a hard 2 s timeout."""

import logging
import os
import time

import requests

import clients.otel as otel

logger = logging.getLogger(__name__)

TIMEOUT_S = 2
_KEYS = ("message_count", "my_response_count", "relationship_label", "notes")


def get_person(email: str) -> dict | None:
    base = os.environ.get("PEOPLE_API_URL", "").rstrip("/")
    if not base:
        otel.people_lookup.add(1, {"outcome": "disabled"})
        return None
    addr = (email or "").strip().lower()
    t0 = time.monotonic()
    outcome = "error"
    try:
        resp = requests.get(
            f"{base}/people/{addr}",
            headers={"Authorization": f"Bearer {os.environ.get('PEOPLE_API_TOKEN', '')}"},
            timeout=TIMEOUT_S,
        )
        if resp.status_code == 404:
            outcome = "miss"
            return None
        resp.raise_for_status()
        data = resp.json()
        outcome = "hit"
        return {k: data.get(k) for k in _KEYS}
    except Exception:
        logger.warning("people-api lookup failed for %s", addr, exc_info=True)
        return None
    finally:
        otel.people_lookup.add(1, {"outcome": outcome})
        otel.people_lookup_duration.record((time.monotonic() - t0) * 1000)
