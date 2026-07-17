from datetime import datetime, timezone

import services.retriage as retriage


class FakeEmail:
    subject = "Server down"
    from_display = "Ops <ops@example.com>"
    received_date = "2026-07-10 12:00:00"

    def get_body_text(self):
        return "The server is down, please fix ASAP." * 200  # long -> exercises trim


class FakeClient:
    def __init__(self, email=FakeEmail(), reply=None):
        self._email = email
        self._reply = reply

    def get_email_details(self, message_id):
        return self._email

    def latest_reply_from_me(self, conversation_id, after):
        return self._reply


NOW = datetime(2026, 7, 14, 5, 0, tzinfo=timezone.utc)
RECEIVED = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)


def test_evaluate_returns_verdict_with_reply_excerpt(monkeypatch):
    seen = {}

    def fake_verdict(system_prompt, user_message):
        seen["user"] = user_message
        return {"verdict": "resolved_or_expired", "reason": "owner says it is fixed"}

    monkeypatch.setattr(retriage, "retriage_verdict", fake_verdict)
    v = retriage.evaluate(
        FakeClient(reply="Fixed it yesterday, all good."), "m1", "conv1", RECEIVED, NOW
    )
    assert v == "resolved_or_expired"
    assert "Server down" in seen["user"]
    assert "Fixed it yesterday, all good." in seen["user"]  # reply excerpt rendered
    assert len(seen["user"]) < 10000  # body trimmed


def test_evaluate_renders_no_reply_line(monkeypatch):
    seen = {}

    def fake_verdict(system_prompt, user_message):
        seen["user"] = user_message
        return {"verdict": "still_urgent"}

    monkeypatch.setattr(retriage, "retriage_verdict", fake_verdict)
    retriage.evaluate(FakeClient(reply=None), "m1", "conv1", RECEIVED, NOW)
    assert "has not replied" in seen["user"]


def test_evaluate_fail_safe_on_claude_error(monkeypatch):
    def boom(system_prompt, user_message):
        raise ValueError("bad json")

    monkeypatch.setattr(retriage, "retriage_verdict", boom)
    assert retriage.evaluate(FakeClient(), "m1", "conv1", RECEIVED, NOW) == "still_urgent"


def test_evaluate_fail_safe_on_fetch_failure(monkeypatch):
    class NoEmail(FakeClient):
        def get_email_details(self, message_id):
            return None

    assert retriage.evaluate(NoEmail(), "m1", "conv1", RECEIVED, NOW) == "still_urgent"


def test_evaluate_handles_missing_conversation(monkeypatch):
    monkeypatch.setattr(
        retriage, "retriage_verdict", lambda s, u: {"verdict": "needs_response"}
    )
    assert retriage.evaluate(FakeClient(), "m1", None, RECEIVED, NOW) == "needs_response"
