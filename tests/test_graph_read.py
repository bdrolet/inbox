import pytest
import requests
from datetime import datetime, timezone

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
    """Bare client: skip __init__ (env vars + MSAL); set only what reads use."""
    c = GraphEmailClient.__new__(GraphEmailClient)
    c.graph_endpoint = "https://graph.microsoft.com/v1.0"
    c.access_token = "token"
    return c


def test_get_email_details_default_hits_me(monkeypatch):
    seen = {}

    def fake_get(url, headers=None, params=None):
        seen["url"] = url
        return _Resp(json_data={"id": "m1", "subject": "s"})

    monkeypatch.setattr(requests, "get", fake_get)
    email = _client().get_email_details("m1")
    assert seen["url"] == "https://graph.microsoft.com/v1.0/me/messages/m1"
    assert email.id == "m1"


def test_get_email_details_shared_hits_users_path(monkeypatch):
    seen = {}

    def fake_get(url, headers=None, params=None):
        seen["url"] = url
        return _Resp(json_data={"id": "m1"})

    monkeypatch.setattr(requests, "get", fake_get)
    _client().get_email_details("m1", mailbox="team@x.com")
    assert seen["url"] == "https://graph.microsoft.com/v1.0/users/team@x.com/messages/m1"


def test_get_email_details_404_returns_none(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(status_code=404))
    assert _client().get_email_details("gone") is None


def test_get_email_details_403_raises(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(status_code=403, text="denied"))
    with pytest.raises(requests.exceptions.HTTPError):
        _client().get_email_details("m1", mailbox="team@x.com")


def test_get_attachments_shared_hits_users_path(monkeypatch):
    seen = {}

    def fake_get(url, headers=None, params=None):
        seen["url"] = url
        return _Resp(json_data={"value": [{"id": "a1"}]})

    monkeypatch.setattr(requests, "get", fake_get)
    atts = _client().get_attachments("m1", mailbox="team@x.com")
    assert (
        seen["url"] == "https://graph.microsoft.com/v1.0/users/team@x.com/messages/m1/attachments"
    )
    assert atts == [{"id": "a1"}]


def test_get_attachments_404_raises_lookup(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(status_code=404))
    with pytest.raises(LookupError):
        _client().get_attachments("gone")


def test_get_attachments_403_raises_http(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(status_code=403))
    with pytest.raises(requests.exceptions.HTTPError):
        _client().get_attachments("m1", mailbox="team@x.com")


def test_get_email_details_quotes_mailbox_path_segment(monkeypatch):
    seen = {}

    def fake_get(url, headers=None, params=None):
        seen["url"] = url
        return _Resp(json_data={"id": "m1"})

    monkeypatch.setattr(requests, "get", fake_get)
    _client().get_email_details("m1", mailbox="a/b@x.com")
    assert "/users/a%2Fb@x.com" in seen["url"]


def test_get_member_groups_403_returns_empty_by_default(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(status_code=403))
    assert _client().get_member_groups() == []


def test_get_member_groups_403_raises_when_requested(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(status_code=403))
    with pytest.raises(requests.exceptions.HTTPError):
        _client().get_member_groups(raise_on_error=True)


def test_list_inbox_categories_includes_received_and_conversation(monkeypatch):
    seen = {}

    def fake_get(url, headers=None, params=None):
        seen["url"] = url
        return _Resp(
            json_data={
                "value": [
                    {
                        "id": "m1",
                        "categories": ["urgent", "P0"],
                        "receivedDateTime": "2026-07-10T12:00:00Z",
                        "conversationId": "conv1",
                    },
                    {"id": "m2"},
                ]
            }
        )

    monkeypatch.setattr(requests, "get", fake_get)
    rows = _client().list_inbox_categories()
    assert "receivedDateTime,conversationId" in seen["url"] or "receivedDateTime" in seen["url"]
    assert rows[0] == {
        "id": "m1",
        "categories": ["urgent", "P0"],
        "receivedDateTime": "2026-07-10T12:00:00Z",
        "conversationId": "conv1",
    }
    assert rows[1] == {"id": "m2", "categories": [], "receivedDateTime": None, "conversationId": None}


def test_latest_reply_from_me_returns_newest_preview(monkeypatch):
    def fake_get(url, headers=None, params=None):
        assert "sentitems" in url
        assert params["$filter"] == "conversationId eq 'conv1'"
        return _Resp(
            json_data={
                "value": [
                    {"id": "s1", "sentDateTime": "2026-07-11T09:00:00Z",
                     "bodyPreview": "older reply"},
                    {"id": "s2", "sentDateTime": "2026-07-12T09:00:00Z",
                     "bodyPreview": "on it, will finish Friday"},
                ]
            }
        )

    monkeypatch.setattr(requests, "get", fake_get)
    after = datetime(2026, 7, 10, tzinfo=timezone.utc)
    assert _client().latest_reply_from_me("conv1", after) == "on it, will finish Friday"


def test_latest_reply_from_me_none_when_only_older_replies(monkeypatch):
    def fake_get(url, headers=None, params=None):
        return _Resp(
            json_data={
                "value": [{"id": "s1", "sentDateTime": "2026-07-09T09:00:00Z",
                           "bodyPreview": "before it arrived"}]
            }
        )

    monkeypatch.setattr(requests, "get", fake_get)
    after = datetime(2026, 7, 10, tzinfo=timezone.utc)
    assert _client().latest_reply_from_me("conv1", after) is None


def test_latest_reply_from_me_none_on_graph_error(monkeypatch):
    def fake_get(url, headers=None, params=None):
        raise requests.exceptions.ConnectionError("boom")

    monkeypatch.setattr(requests, "get", fake_get)
    after = datetime(2026, 7, 10, tzinfo=timezone.utc)
    assert _client().latest_reply_from_me("conv1", after) is None
