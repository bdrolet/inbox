import sys
import types

import pytest

# Stub the DB module the service imports at load time (clients.db -> psycopg,
# which needs libpq and isn't available in CI). setdefault so we cooperate with
# the identical stub installed by other test modules.
_fake_db = types.ModuleType("clients.db")
_fake_db.get_conn = lambda: None
sys.modules.setdefault("clients.db", _fake_db)

import services.calendar_response as calendar_response  # noqa: E402


class FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeGraph:
    def __init__(self):
        self.calls = []

    def accept_event(self, ical_uid):
        self.calls.append(("accept_event", ical_uid))

    def decline_event(self, ical_uid):
        self.calls.append(("decline_event", ical_uid))

    def tentatively_accept_event(self, ical_uid):
        self.calls.append(("tentatively_accept_event", ical_uid))


def _row() -> dict:
    return {
        "message_id": "m1",
        "graph_message_id": "g1",
        "ical_uid": "u1",
        "title": "Standup",
        "start_time": None,
        "end_time": None,
        "timezone": "UTC",
        "organizer": "alice@b.com",
        "zoom_link": "https://zoom.us/j/1",
        "location": None,
    }


class Wiring:
    def __init__(self, graph, responses):
        self.graph = graph
        self.responses = responses


@pytest.fixture
def wired(monkeypatch):
    """Patch the service's DB, repo and Graph seams; capture what it calls."""
    graph = FakeGraph()
    responses: list[tuple] = []

    monkeypatch.setattr(calendar_response, "get_conn", lambda: FakeConn())
    monkeypatch.setattr(calendar_response, "get_graph_client", lambda: graph)
    monkeypatch.setattr(calendar_response.repo_cal, "get_by_message_id", lambda conn, mid: _row())
    monkeypatch.setattr(
        calendar_response.repo_cal,
        "set_response",
        lambda conn, mid, resp: responses.append((mid, resp)),
    )
    return Wiring(graph, responses)


def test_accept_sends_graph_rsvp_and_records_response(wired):
    calendar_response.apply("m1", "accept")

    assert wired.graph.calls == [("accept_event", "u1")]
    assert wired.responses == [("m1", "accept")]


def test_accept_does_not_touch_google_calendar(wired):
    """Google Calendar events are owned by the schedule service now."""
    calendar_response.apply("m1", "accept")

    # The deleted client must not be importable, nor pulled in as a side effect.
    assert "clients.google_calendar" not in sys.modules
    assert not hasattr(calendar_response, "gcal")
    # No Google API client library is reachable from this code path either.
    assert "googleapiclient" not in sys.modules


def test_decline_sends_decline(wired):
    calendar_response.apply("m1", "decline")

    assert wired.graph.calls == [("decline_event", "u1")]
    assert wired.responses == [("m1", "decline")]


def test_maybe_sends_tentative(wired):
    calendar_response.apply("m1", "maybe")

    assert wired.graph.calls == [("tentatively_accept_event", "u1")]
    assert wired.responses == [("m1", "maybe")]


def test_unknown_action_is_a_noop(wired):
    calendar_response.apply("m1", "explode")

    assert wired.graph.calls == []
    assert wired.responses == []


def test_missing_invite_row_returns_early(wired, monkeypatch):
    monkeypatch.setattr(calendar_response.repo_cal, "get_by_message_id", lambda conn, mid: None)

    calendar_response.apply("m1", "accept")

    assert wired.graph.calls == []
    assert wired.responses == []
