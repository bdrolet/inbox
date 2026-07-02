import pytest
import requests

from clients.azure.graph_email_client import GraphEmailClient


class _Resp:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.text = text

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            err = requests.exceptions.HTTPError(str(self.status_code))
            err.response = self
            raise err


def _client() -> GraphEmailClient:
    c = GraphEmailClient.__new__(GraphEmailClient)
    c.graph_endpoint = "https://graph.microsoft.com/v1.0"
    c.access_token = "token"
    return c


def _graph_fake(routes):
    """Return a fake requests.get dispatching on URL substring."""

    def fake_get(url, headers=None, params=None):
        for fragment, resp in routes.items():
            if fragment in url:
                return resp
        raise AssertionError(f"unexpected URL: {url}")

    return fake_get


def test_get_group_conversation_assembles_posts(monkeypatch):
    routes = {
        "/threads/t1/posts": _Resp(
            json_data={"value": [{"id": "p1", "receivedDateTime": "2026-07-01T10:00:00Z"}]}
        ),
        "/conversations/c1/threads": _Resp(json_data={"value": [{"id": "t1"}]}),
        "/conversations/c1": _Resp(
            json_data={"topic": "Lunch", "lastDeliveredDateTime": "2026-07-01T10:00:00Z"}
        ),
    }
    monkeypatch.setattr(requests, "get", _graph_fake(routes))
    convo = _client().get_group_conversation("g1", "c1")
    assert convo["topic"] == "Lunch"
    assert convo["posts"] == [
        {"id": "p1", "receivedDateTime": "2026-07-01T10:00:00Z", "threadId": "t1"}
    ]


def test_get_group_conversation_404_returns_none(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(status_code=404))
    assert _client().get_group_conversation("g1", "gone") is None


def test_get_group_conversation_403_raises(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(status_code=403))
    with pytest.raises(requests.exceptions.HTTPError):
        _client().get_group_conversation("g1", "c1")


def test_get_group_post_attachments(monkeypatch):
    seen = {}

    def fake_get(url, headers=None, params=None):
        seen["url"] = url
        return _Resp(json_data={"value": [{"id": "a1", "name": "f.pdf"}]})

    monkeypatch.setattr(requests, "get", fake_get)
    atts = _client().get_group_post_attachments("g1", "t1", "p1")
    assert (
        seen["url"] == "https://graph.microsoft.com/v1.0/groups/g1/threads/t1/posts/p1/attachments"
    )
    assert atts == [{"id": "a1", "name": "f.pdf"}]


def test_get_group_post_attachments_404_raises_lookup(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(status_code=404))
    with pytest.raises(LookupError):
        _client().get_group_post_attachments("g1", "t1", "gone")
