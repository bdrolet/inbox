import json
import sys
import types
from datetime import UTC, datetime
from pathlib import Path

import pytest

# Same libpq-free stubs as tests/test_sent_handler.py (session-persistent).
_fake_db = types.ModuleType("clients.db")
_fake_db.get_conn = lambda: None
sys.modules.setdefault("clients.db", _fake_db)

import clients.otel as otel  # noqa: E402,F401
import handlers.bounce as bounce  # noqa: E402
from services import email_events  # noqa: E402

_FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str):
    d = json.loads((_FIXTURES / name).read_text())
    msg = {
        "id": "db-1",
        "external_id": "ndr-g1",
        "conversation_id": "conv1",
        "sender": d["from"]["emailAddress"]["address"],
        "subject": d["subject"],
        "to": [r["emailAddress"]["address"] for r in d["toRecipients"]],
        "cc": [],
        "body": d["body"]["content"],
        "received_at": datetime(2026, 9, 3, tzinfo=UTC),
        "has_attachments": True,
    }
    return msg, d["attachments"]


class FakeGraph:
    def __init__(self, result="orig1", exc=None):
        self.result, self.exc, self.calls = result, exc, []

    def sent_message_id_in_conversation(self, conversation_id):
        self.calls.append(conversation_id)
        if self.exc:
            raise self.exc
        return self.result


@pytest.fixture
def published(monkeypatch):
    out = []
    monkeypatch.setattr(email_events, "publish", lambda ev: out.append(ev))
    return out


def _stub_attachments(monkeypatch, atts):
    monkeypatch.setattr(bounce, "fetch_attachments", lambda external_id: atts)


def test_ndr_publishes_one_event_with_original_id(monkeypatch, published):
    msg, atts = _load("ndr_exchange.json")
    _stub_attachments(monkeypatch, atts)
    graph = FakeGraph("orig1")
    assert bounce.maybe_publish(msg, graph) is True
    assert len(published) == 1 and published[0]["original_graph_message_id"] == "orig1"
    assert published[0]["event"] == "email_bounced"
    assert graph.calls == ["conv1"]


def test_ndr_without_sent_message_publishes_null_original(monkeypatch, published):
    msg, atts = _load("ndr_exchange.json")
    _stub_attachments(monkeypatch, atts)
    assert bounce.maybe_publish(msg, FakeGraph(None)) is True
    assert published[0]["original_graph_message_id"] is None


def test_non_ndr_publishes_nothing(monkeypatch, published):
    msg = {
        "sender": "alice@example.com",
        "subject": "Lunch?",
        "external_id": "g2",
        "has_attachments": False,
    }
    _stub_attachments(monkeypatch, [])
    assert bounce.maybe_publish(msg, FakeGraph()) is False and published == []


def test_graph_failure_during_resolution_still_publishes(monkeypatch, published):
    msg, atts = _load("ndr_exchange.json")
    _stub_attachments(monkeypatch, atts)
    assert bounce.maybe_publish(msg, FakeGraph(exc=RuntimeError("boom"))) is True
    assert published[0]["original_graph_message_id"] is None


def test_attachment_fetch_failure_does_not_raise(monkeypatch, published):
    msg, _ = _load("ndr_exchange.json")

    def boom(external_id):
        raise RuntimeError("graph down")

    monkeypatch.setattr(bounce, "fetch_attachments", boom)
    # Sender is not an NDR sender and subject is "Undeliverable:" so it is
    # still an NDR by subject, but the body cannot be parsed without the DSN.
    assert isinstance(bounce.maybe_publish(msg, FakeGraph()), bool)


def test_no_attachment_fetch_without_has_attachments(monkeypatch, published):
    msg, _ = _load("ndr_plain.json")
    msg["has_attachments"] = False

    def boom(external_id):
        raise AssertionError("must not fetch")

    monkeypatch.setattr(bounce, "fetch_attachments", boom)
    bounce.maybe_publish(msg, FakeGraph())
