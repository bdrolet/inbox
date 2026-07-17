from datetime import datetime
from zoneinfo import ZoneInfo

from services.sweep_rules import (
    apply_verdict,
    decide,
    folder_for_category,
    parse_graph_datetime,
)

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


def _received(y, m, d, hh=12):
    return datetime(y, m, d, hh, 0, tzinfo=ET)


def test_parse_graph_datetime():
    dt = parse_graph_datetime("2026-07-10T12:00:00Z")
    assert dt is not None and dt.tzinfo is not None
    assert parse_graph_datetime(None) is None
    assert parse_graph_datetime("garbage") is None


def test_decide_retriages_stale_urgent():
    # received 07-10 noon, sweep 07-14 5 AM -> older than 3 days
    d = decide(["urgent", "P0"], _now(2026, 7, 14), _received(2026, 7, 10))
    assert d.action == "retriage"


def test_decide_skips_fresh_urgent():
    # received 07-12, sweep 07-14 -> under 3 days
    assert decide(["urgent", "P0"], _now(2026, 7, 14), _received(2026, 7, 12)).action == "skip"


def test_decide_urgent_without_received_at_skips():
    assert decide(["urgent", "P0"], _now(2026, 7, 14), None).action == "skip"


def test_decide_keep_until_defers_retriage():
    d = decide(
        ["urgent", "keep_until:2026-07-20"], _now(2026, 7, 14), _received(2026, 7, 1)
    )
    assert d.action == "hold"


def test_decide_republishes_old_untagged():
    d = decide(["P2", "newsletter"], _now(2026, 7, 14), _received(2026, 7, 12))
    assert d.action == "republish"
    assert decide([], _now(2026, 7, 14), _received(2025, 1, 1)).action == "republish"


def test_decide_skips_fresh_or_undated_untagged():
    # under 24h old
    assert decide([], _now(2026, 7, 14), _received(2026, 7, 13, 12)).action == "skip"
    # no received_at -> fail safe
    assert decide([], _now(2026, 7, 14), None).action == "skip"


def test_decide_untagged_with_any_keep_until_never_republishes():
    # even an elapsed keep_until means "Ben touched this" -> leave it alone
    d = decide(["keep_until:2026-01-01"], _now(2026, 7, 14), _received(2026, 1, 1))
    assert d.action == "skip"


def test_decide_tagged_messages_unchanged():
    d = decide(["reference", "P3"], _now(2026, 7, 14), _received(2026, 7, 1))
    assert d.action == "move" and d.folder == "Archive"


def test_apply_verdict_still_urgent_adds_hold():
    out = apply_verdict("still_urgent", ["urgent", "P0"], _now(2026, 7, 14))
    assert out.folder is None
    assert out.new_categories == ["urgent", "P0", "keep_until:2026-07-17"]
    assert out.verdict == "still_urgent"


def test_apply_verdict_still_urgent_replaces_old_hold():
    out = apply_verdict(
        "still_urgent", ["urgent", "keep_until:2026-07-10"], _now(2026, 7, 14)
    )
    assert out.new_categories == ["urgent", "keep_until:2026-07-17"]


def test_apply_verdict_needs_response_demotes():
    out = apply_verdict("needs_response", ["urgent", "P0"], _now(2026, 7, 14))
    assert out.folder == "reply_required"
    assert out.new_categories == ["respond", "P0"]


def test_apply_verdict_resolved_archives():
    out = apply_verdict(
        "resolved_or_expired", ["urgent", "P0", "keep_until:2026-07-01"], _now(2026, 7, 14)
    )
    assert out.folder == "Archive"
    assert out.new_categories == ["P0"]


def test_apply_verdict_unknown_treated_as_still_urgent():
    out = apply_verdict("banana", ["urgent"], _now(2026, 7, 14))
    assert out.folder is None
    assert out.verdict == "still_urgent"
    assert out.new_categories == ["urgent", "keep_until:2026-07-17"]
