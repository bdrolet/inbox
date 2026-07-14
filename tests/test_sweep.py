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
