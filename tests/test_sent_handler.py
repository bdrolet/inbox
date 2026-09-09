import sys
import types
from datetime import UTC, datetime

import pytest

# Same libpq-free stubs as tests/test_email_events.py (session-persistent).
_fake_db = types.ModuleType("clients.db")
_fake_db.get_conn = lambda: None
sys.modules.setdefault("clients.db", _fake_db)

import clients.otel as otel  # noqa: E402,F401
import handlers.sent as sent  # noqa: E402
from services import email_events  # noqa: E402


class E:
    def __init__(self, subject="Re: hi"):
        self.id = "g1"
        self.conversation_id = None
        self.sent_datetime = datetime(2026, 9, 3, tzinfo=UTC)
        self.from_email = "ben@drolet.cloud"
        self.to_recipients = [{"address": "a@x.com"}]
        self.cc_recipients = []
        self.bcc_recipients = []
        self.subject = subject


@pytest.fixture
def wired(monkeypatch):
    published = []
    monkeypatch.setattr(sent, "get_graph_client", lambda: object())
    monkeypatch.setattr(email_events, "publish", lambda ev: published.append(ev))
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    return published


def test_publishes_email_sent(monkeypatch, wired):
    monkeypatch.setattr(sent, "fetch", lambda mid, client: E())
    sent.run({"resourceData": {"id": "g1"}})
    assert len(wired) == 1 and wired[0]["event"] == "email_sent" and wired[0]["to"] == ["a@x.com"]


def test_missing_id_is_skipped(monkeypatch, wired):
    sent.run({"resourceData": {}})
    assert wired == []


def test_fetch_none_is_skipped(monkeypatch, wired):
    monkeypatch.setattr(sent, "fetch", lambda mid, client: None)
    sent.run({"resourceData": {"id": "g1"}})
    assert wired == []


def test_local_test_subject_skipped_in_gcp(monkeypatch, wired):
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setattr(sent, "fetch", lambda mid, client: E("[LOCAL-TEST] hi"))
    sent.run({"resourceData": {"id": "g1"}})
    assert wired == []


def test_local_test_subject_published_outside_gcp(monkeypatch, wired):
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    monkeypatch.setattr(sent, "fetch", lambda mid, client: E("[LOCAL-TEST] hi"))
    sent.run({"resourceData": {"id": "g1"}})
    assert len(wired) == 1 and wired[0]["subject"] == "[LOCAL-TEST] hi"
