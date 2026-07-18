"""Morning sweep orchestration: list Inbox, decide per message, move/strip,
re-triage stale urgent mail, republish untagged mail for classification.

Pure of GCP/functions-framework deps so it is unit-testable in CI. The Cloud
Function entry point in main.py builds the Graph client, the retriage
evaluator, and the Pub/Sub republish callable, and calls run_sweep.
"""

import logging
from datetime import datetime
from typing import Any, Callable

import services.sweep_rules as rules
from services.sweep_rules import apply_verdict, decide, parse_graph_datetime

logger = logging.getLogger(__name__)

Evaluate = Callable[[Any, str, str | None, datetime, datetime], str]
Republish = Callable[[str], None]


def run_sweep(
    client: Any,
    now: datetime,
    evaluate: Evaluate | None = None,
    republish: Republish | None = None,
) -> dict[str, int]:
    counts = {
        "moved": 0,
        "held": 0,
        "skipped": 0,
        "errored": 0,
        "retriaged_kept": 0,
        "retriaged_demoted": 0,
        "retriaged_archived": 0,
        "republished": 0,
    }
    attempts = {"republish": 0}
    for msg in client.list_inbox_categories():
        received_at = parse_graph_datetime(msg.get("receivedDateTime"))
        d = decide(msg.get("categories", []), now, received_at)
        if d.action == "hold":
            counts["held"] += 1
            continue
        if d.action == "skip":
            counts["skipped"] += 1
            continue
        if d.action == "retriage":
            _retriage(client, msg, received_at, now, evaluate, counts)
            continue
        if d.action == "republish":
            _republish(msg, republish, counts, attempts)
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


def _retriage(client, msg, received_at, now, evaluate, counts) -> None:
    if evaluate is None or received_at is None:
        counts["skipped"] += 1
        return
    try:
        verdict = evaluate(client, msg["id"], msg.get("conversationId"), received_at, now)
        outcome = apply_verdict(verdict, msg.get("categories", []), now)
        if outcome.folder is None:
            if client.set_categories(msg["id"], outcome.new_categories):
                counts["retriaged_kept"] += 1
            else:
                counts["errored"] += 1
                logger.warning("sweep: retriage tag update failed for %s", msg["id"])
            return
        moved = client.move_message_to_action_folder(msg["id"], outcome.folder)
        if moved is None:
            counts["errored"] += 1
            logger.warning("sweep: retriage move failed for %s", msg["id"])
            return
        if not client.set_categories(moved.get("id", msg["id"]), outcome.new_categories):
            counts["errored"] += 1
            logger.warning("sweep: retriage tag update failed for %s", msg["id"])
            return
        if outcome.folder == "Archive":
            counts["retriaged_archived"] += 1
        else:
            counts["retriaged_demoted"] += 1
    except Exception:
        counts["errored"] += 1
        logger.warning("sweep: retriage failed for %s", msg["id"], exc_info=True)


def _republish(msg, republish, counts, attempts) -> None:
    if republish is None or attempts["republish"] >= rules.REPUBLISH_NIGHTLY_CAP:
        counts["skipped"] += 1
        return
    attempts["republish"] += 1
    try:
        republish(msg["id"])
        counts["republished"] += 1
    except Exception:
        counts["errored"] += 1
        logger.warning("sweep: republish failed for %s", msg["id"], exc_info=True)
