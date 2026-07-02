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
