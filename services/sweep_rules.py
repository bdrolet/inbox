"""Pure decision logic for the morning sweep. No I/O — safe to import in CI."""

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
KEEP_UNTIL_PREFIX = "keep_until:"

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
    action: str  # "move" | "hold" | "skip"
    folder: str | None = None
    strip_categories: list[str] = field(default_factory=list)


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


def decide(categories: list[str], now: datetime) -> SweepDecision:
    keep_tags = [c for c in categories if c.startswith(KEEP_UNTIL_PREFIX)]
    for tag in keep_tags:
        elapses = _parse_keep_until(tag[len(KEEP_UNTIL_PREFIX) :])
        if elapses is None or now < elapses:
            return SweepDecision(action="hold")

    for c in categories:
        folder = folder_for_category(c)
        if folder is not None:
            return SweepDecision(action="move", folder=folder, strip_categories=keep_tags)
    return SweepDecision(action="skip")
