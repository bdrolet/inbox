from datetime import datetime
from zoneinfo import ZoneInfo

from services.sweep_rules import decide, folder_for_category

ET = ZoneInfo("America/New_York")


def _now(y, m, d, hh=5, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=ET)


def test_folder_for_category():
    assert folder_for_category("reference") == "Archive"
    assert folder_for_category("ignore") == "Archive"
    assert folder_for_category("respond") == "reply_required"
    assert folder_for_category("review") == "review"
    assert folder_for_category("urgent") is None
    assert folder_for_category("nonsense") is None


def test_decide_moves_tagged_message():
    d = decide(["reference", "P3"], _now(2026, 7, 14))
    assert d.action == "move"
    assert d.folder == "Archive"
    assert d.strip_categories == []


def test_decide_skips_urgent_and_untagged():
    assert decide(["urgent", "P0"], _now(2026, 7, 14)).action == "skip"
    assert decide(["P2", "newsletter"], _now(2026, 7, 14)).action == "skip"
    assert decide([], _now(2026, 7, 14)).action == "skip"


def test_decide_holds_future_keep_until_bare_date():
    # held through end of 2026-07-20 -> at 5 AM on 07-14 it is a hold
    d = decide(["respond", "keep_until:2026-07-20"], _now(2026, 7, 14))
    assert d.action == "hold"


def test_decide_files_after_bare_date_elapses():
    # first sweep strictly after 2026-07-20 -> file on 07-21
    d = decide(["respond", "keep_until:2026-07-20"], _now(2026, 7, 21))
    assert d.action == "move"
    assert d.folder == "reply_required"
    assert d.strip_categories == ["keep_until:2026-07-20"]


def test_decide_datetime_keep_until_boundary():
    tag = "keep_until:2026-07-14T09:00"
    assert decide(["review", tag], _now(2026, 7, 14, 5, 0)).action == "hold"
    assert decide(["review", tag], _now(2026, 7, 14, 9, 0)).action == "move"


def test_decide_unparseable_keep_until_holds():
    assert decide(["respond", "keep_until:not-a-date"], _now(2026, 7, 14)).action == "hold"
