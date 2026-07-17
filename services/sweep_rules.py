"""Pure decision logic for the morning sweep. No I/O — safe to import in CI."""

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from models.types import Category

ET = ZoneInfo("America/New_York")
KEEP_UNTIL_PREFIX = "keep_until:"

RETRIAGE_AFTER = timedelta(days=3)
REPUBLISH_AFTER = timedelta(hours=24)
RETRIAGE_HOLD_DAYS = 3
REPUBLISH_NIGHTLY_CAP = 50
KNOWN_CATEGORIES = {c.value for c in Category}

_CATEGORY_FOLDER = {
    "reference": "Archive",
    "ignore": "Archive",
    "respond": "reply_required",
    "review": "review",
    # "urgent" intentionally absent -> no move
}


def folder_for_category(category: str) -> str | None:
    return _CATEGORY_FOLDER.get(category)


@dataclass
class SweepDecision:
    action: Literal["move", "hold", "skip", "retriage", "republish"]
    folder: str | None = None
    strip_categories: list[str] = field(default_factory=list)


@dataclass
class RetriageOutcome:
    verdict: str
    folder: str | None
    new_categories: list[str]


def _parse_keep_until(value: str) -> datetime | None:
    """Parse the value after 'keep_until:'. Returns the ET instant at which the
    hold elapses, or None if unparseable. A bare date holds through the end of
    that day (elapses at 00:00 ET the next day); a datetime elapses at that
    exact ET instant."""
    raw = value.strip()
    try:
        if "T" in raw:
            dt = datetime.fromisoformat(raw)
            return dt.replace(tzinfo=ET) if dt.tzinfo is None else dt.astimezone(ET)
        d = datetime.fromisoformat(raw).date()
        return datetime.combine(d, time(0, 0), tzinfo=ET) + timedelta(days=1)
    except ValueError:
        return None


def parse_graph_datetime(value: str | None) -> datetime | None:
    """Graph ISO timestamp ('...Z') -> aware datetime, or None if absent/bad."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def apply_verdict(verdict: str, categories: list[str], now: datetime) -> RetriageOutcome:
    """Map a re-triage verdict onto concrete tag/folder changes. Unknown
    verdicts fail safe to still_urgent (nothing leaves the Inbox)."""
    base = [c for c in categories if not c.startswith(KEEP_UNTIL_PREFIX)]
    if verdict == "needs_response":
        return RetriageOutcome(
            verdict=verdict,
            folder="reply_required",
            new_categories=["respond" if c == "urgent" else c for c in base],
        )
    if verdict == "resolved_or_expired":
        return RetriageOutcome(
            verdict=verdict,
            folder="Archive",
            new_categories=[c for c in base if c != "urgent"],
        )
    hold = (now + timedelta(days=RETRIAGE_HOLD_DAYS)).date().isoformat()
    return RetriageOutcome(
        verdict="still_urgent",
        folder=None,
        new_categories=base + [f"{KEEP_UNTIL_PREFIX}{hold}"],
    )


def decide(
    categories: list[str], now: datetime, received_at: datetime | None = None
) -> SweepDecision:
    keep_tags = [c for c in categories if c.startswith(KEEP_UNTIL_PREFIX)]
    for tag in keep_tags:
        elapses = _parse_keep_until(tag[len(KEEP_UNTIL_PREFIX) :])
        if elapses is None or now < elapses:
            return SweepDecision(action="hold")

    for c in categories:
        folder = folder_for_category(c)
        if folder is not None:
            return SweepDecision(action="move", folder=folder, strip_categories=keep_tags)

    if "urgent" in categories:
        if received_at is not None and now - received_at > RETRIAGE_AFTER:
            return SweepDecision(action="retriage")
        return SweepDecision(action="skip")

    tagged = any(c in KNOWN_CATEGORIES for c in categories)
    if not tagged and not keep_tags:
        if received_at is not None and now - received_at > REPUBLISH_AFTER:
            return SweepDecision(action="republish")
    return SweepDecision(action="skip")
