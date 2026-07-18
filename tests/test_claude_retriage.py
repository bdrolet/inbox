import pytest

import clients.claude as claude


class _FakeUsage:
    input_tokens = 10
    output_tokens = 5
    cache_creation_input_tokens = 0
    cache_read_input_tokens = 0


class _FakeContent:
    def __init__(self, text):
        self.text = text


class _FakeResponse:
    def __init__(self, text):
        self.usage = _FakeUsage()
        self.content = [_FakeContent(text)]


class _FakeMessages:
    def __init__(self, text):
        self._text = text

    def create(self, **kwargs):
        return _FakeResponse(self._text)


class _FakeAnthropic:
    def __init__(self, text):
        self.messages = _FakeMessages(text)


def _with_response(monkeypatch, text):
    monkeypatch.setattr(claude, "_client", _FakeAnthropic(text))


def test_retriage_verdict_parses_raw_json(monkeypatch):
    _with_response(monkeypatch, '{"verdict": "needs_response", "reason": "r"}')
    data = claude.retriage_verdict("sys", "user")
    assert data["verdict"] == "needs_response"


def test_retriage_verdict_strips_json_code_fence(monkeypatch):
    _with_response(
        monkeypatch,
        '```json\n{"verdict": "still_urgent", "reason": "active incident"}\n```',
    )
    data = claude.retriage_verdict("sys", "user")
    assert data["verdict"] == "still_urgent"


def test_retriage_verdict_strips_bare_code_fence(monkeypatch):
    _with_response(monkeypatch, '```\n{"verdict": "resolved_or_expired"}\n```')
    data = claude.retriage_verdict("sys", "user")
    assert data["verdict"] == "resolved_or_expired"


def test_retriage_verdict_raises_on_non_json(monkeypatch):
    _with_response(monkeypatch, "definitely not json")
    with pytest.raises(ValueError):
        claude.retriage_verdict("sys", "user")


def test_retriage_verdict_raises_on_missing_verdict(monkeypatch):
    _with_response(monkeypatch, '{"reason": "no verdict key"}')
    with pytest.raises(ValueError):
        claude.retriage_verdict("sys", "user")
