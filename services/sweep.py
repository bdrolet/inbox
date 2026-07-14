"""Morning sweep orchestration: list Inbox, decide per message, move/strip.

Pure of GCP/functions-framework deps so it is unit-testable in CI. The Cloud
Function entry point in main.py builds the Graph client and calls run_sweep.
"""

import logging
from datetime import datetime
from typing import Any

from services.sweep_rules import decide

logger = logging.getLogger(__name__)


def run_sweep(client: Any, now: datetime) -> dict[str, int]:
    counts = {"moved": 0, "held": 0, "skipped": 0, "errored": 0}
    for msg in client.list_inbox_categories():
        d = decide(msg.get("categories", []), now)
        if d.action == "hold":
            counts["held"] += 1
            continue
        if d.action == "skip":
            counts["skipped"] += 1
            continue
        # action == "move"
        moved = client.move_message_to_action_folder(msg["id"], d.folder)
        if moved is None:
            counts["errored"] += 1
            logger.warning("sweep: move failed for %s -> %s", msg["id"], d.folder)
            continue
        counts["moved"] += 1
        if d.strip_categories:
            remaining = [c for c in msg.get("categories", []) if c not in d.strip_categories]
            client.set_categories(moved.get("id", msg["id"]), remaining)
    logger.info("sweep complete: %s", counts)
    return counts
