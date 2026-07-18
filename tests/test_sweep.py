from datetime import datetime
from zoneinfo import ZoneInfo

from services.sweep import run_sweep  # pure orchestrator, importable in CI

ET = ZoneInfo("America/New_York")


class FakeClient:
    def __init__(self, messages):
        self._messages = messages
        self.moved = []
        self.stripped = []

    def list_inbox_categories(self):
        return self._messages

    def move_message_to_action_folder(self, msg_id, folder):
        self.moved.append((msg_id, folder))
        return {"id": msg_id}

    def set_categories(self, msg_id, categories):
        self.stripped.append((msg_id, categories))
        return True


def test_run_sweep_moves_holds_skips():
    now = datetime(2026, 7, 21, 5, 0, tzinfo=ET)
    client = FakeClient(
        [
            {"id": "a", "categories": ["reference", "P3"]},
            {"id": "b", "categories": ["urgent", "P0"]},
            {"id": "c", "categories": ["respond", "keep_until:2027-01-01"]},
            {"id": "d", "categories": ["respond", "keep_until:2026-07-20"]},
        ]
    )
    counts = run_sweep(client, now)
    assert ("a", "Archive") in client.moved
    assert ("d", "reply_required") in client.moved
    assert counts["moved"] == 2
    assert counts["held"] == 1  # c
    assert counts["skipped"] == 1  # b (urgent)
    # d had an elapsed keep_until -> stripped to just its category tag
    assert ("d", ["respond"]) in client.stripped


def test_run_sweep_counts_move_errors():
    now = datetime(2026, 7, 21, 5, 0, tzinfo=ET)

    class Boom(FakeClient):
        def move_message_to_action_folder(self, msg_id, folder):
            return None  # signals failure

    client = Boom([{"id": "a", "categories": ["reference"]}])
    counts = run_sweep(client, now)
    assert counts["errored"] == 1
    assert counts["moved"] == 0


def test_run_sweep_retriage_verdict_paths():
    now = datetime(2026, 7, 14, 5, 0, tzinfo=ET)
    stale = "2026-07-01T12:00:00Z"
    client = FakeClient(
        [
            {
                "id": "keep",
                "categories": ["urgent"],
                "receivedDateTime": stale,
                "conversationId": "c1",
            },
            {
                "id": "demote",
                "categories": ["urgent", "P0"],
                "receivedDateTime": stale,
                "conversationId": "c2",
            },
            {
                "id": "done",
                "categories": ["urgent"],
                "receivedDateTime": stale,
                "conversationId": "c3",
            },
        ]
    )
    verdicts = {"keep": "still_urgent", "demote": "needs_response", "done": "resolved_or_expired"}

    def evaluate(c, message_id, conversation_id, received_at, now_):
        return verdicts[message_id]

    counts = run_sweep(client, now, evaluate=evaluate)
    assert counts["retriaged_kept"] == 1
    assert counts["retriaged_demoted"] == 1
    assert counts["retriaged_archived"] == 1
    # kept: fresh keep_until applied in place, no move
    assert ("keep", ["urgent", "keep_until:2026-07-17"]) in client.stripped
    assert all(m[0] != "keep" for m in client.moved)
    # demoted: moved + retagged respond
    assert ("demote", "reply_required") in client.moved
    assert ("demote", ["respond", "P0"]) in client.stripped
    # archived: moved + urgent stripped
    assert ("done", "Archive") in client.moved
    assert ("done", []) in client.stripped


def test_run_sweep_republishes_untagged_with_cap(monkeypatch):
    import services.sweep_rules as rules

    monkeypatch.setattr(rules, "REPUBLISH_NIGHTLY_CAP", 2)
    now = datetime(2026, 7, 14, 5, 0, tzinfo=ET)
    old = "2026-07-01T12:00:00Z"
    client = FakeClient(
        [{"id": f"u{i}", "categories": [], "receivedDateTime": old} for i in range(4)]
    )
    published = []
    counts = run_sweep(client, now, republish=published.append)
    assert counts["republished"] == 2
    assert published == ["u0", "u1"]
    assert counts["skipped"] == 2  # over the cap


def test_run_sweep_grooming_errors_do_not_abort():
    now = datetime(2026, 7, 14, 5, 0, tzinfo=ET)
    stale = "2026-07-01T12:00:00Z"
    client = FakeClient(
        [
            {"id": "boom", "categories": ["urgent"], "receivedDateTime": stale},
            {"id": "a", "categories": ["reference"], "receivedDateTime": stale},
        ]
    )

    def evaluate(*args, **kwargs):
        raise RuntimeError("claude exploded")

    counts = run_sweep(client, now, evaluate=evaluate)
    assert counts["errored"] == 1
    assert ("a", "Archive") in client.moved  # batch continued


def test_run_sweep_republish_caps_attempts_not_successes(monkeypatch):
    import services.sweep_rules as rules

    monkeypatch.setattr(rules, "REPUBLISH_NIGHTLY_CAP", 2)
    now = datetime(2026, 7, 14, 5, 0, tzinfo=ET)
    old = "2026-07-01T12:00:00Z"
    client = FakeClient(
        [{"id": f"u{i}", "categories": [], "receivedDateTime": old} for i in range(4)]
    )

    def always_fails(msg_id):
        raise RuntimeError("publish exploded")

    counts = run_sweep(client, now, republish=always_fails)
    assert counts["errored"] == 2
    assert counts["skipped"] == 2
    assert counts["republished"] == 0


def test_run_sweep_retriage_keep_reports_errored_on_tag_failure():
    now = datetime(2026, 7, 14, 5, 0, tzinfo=ET)
    stale = "2026-07-01T12:00:00Z"

    class UntaggableClient(FakeClient):
        def set_categories(self, msg_id, categories):
            self.stripped.append((msg_id, categories))
            return False

    client = UntaggableClient(
        [
            {
                "id": "keep",
                "categories": ["urgent"],
                "receivedDateTime": stale,
                "conversationId": "c1",
            }
        ]
    )

    def evaluate(c, message_id, conversation_id, received_at, now_):
        return "still_urgent"

    counts = run_sweep(client, now, evaluate=evaluate)
    assert counts["errored"] == 1
    assert counts["retriaged_kept"] == 0


def test_run_sweep_without_grooming_callables_is_safe():
    now = datetime(2026, 7, 14, 5, 0, tzinfo=ET)
    old = "2026-07-01T12:00:00Z"
    client = FakeClient(
        [
            {"id": "u", "categories": ["urgent"], "receivedDateTime": old},
            {"id": "n", "categories": [], "receivedDateTime": old},
        ]
    )
    counts = run_sweep(client, now)  # no evaluate/republish
    assert counts["skipped"] == 2
    assert client.moved == []
