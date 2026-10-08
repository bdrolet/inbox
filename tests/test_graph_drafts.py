import pytest

import clients.azure.graph_email_client as graph_email_client
from clients.azure.graph_email_client import GraphEmailClient, NotADraftError

GRAPH = "https://graph.microsoft.com/v1.0"


def _client():
    c = GraphEmailClient.__new__(GraphEmailClient)
    c.access_token = "tok"
    c.graph_endpoint = GRAPH
    return c


class _Resp:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(str(self.status_code))


def _fake_graph(monkeypatch, *, get_status=200, get_json=None, delete_status=204):
    seen = {"get": [], "delete": []}

    def fake_get(url, headers=None, params=None):
        seen["get"].append((url, params))
        return _Resp(get_status, get_json if get_json is not None else {"id": "d1", "isDraft": True})

    def fake_delete(url, headers=None):
        seen["delete"].append(url)
        return _Resp(delete_status)

    monkeypatch.setattr(graph_email_client.requests, "get", fake_get)
    monkeypatch.setattr(graph_email_client.requests, "delete", fake_delete)
    return seen


def test_delete_draft_checks_is_draft_then_deletes(monkeypatch):
    seen = _fake_graph(monkeypatch)
    assert _client().delete_draft("d1") is None
    assert seen["get"] == [(f"{GRAPH}/me/messages/d1", {"$select": "id,isDraft"})]
    assert seen["delete"] == [f"{GRAPH}/me/messages/d1"]


def test_delete_draft_refuses_non_draft_without_deleting(monkeypatch):
    seen = _fake_graph(monkeypatch, get_json={"id": "m1", "isDraft": False})
    with pytest.raises(NotADraftError):
        _client().delete_draft("m1")
    assert seen["delete"] == []


def test_delete_draft_fails_closed_when_is_draft_missing(monkeypatch):
    seen = _fake_graph(monkeypatch, get_json={"id": "m1"})
    with pytest.raises(NotADraftError):
        _client().delete_draft("m1")
    assert seen["delete"] == []


def test_delete_draft_missing_message_is_lookup_error(monkeypatch):
    seen = _fake_graph(monkeypatch, get_status=404)
    with pytest.raises(LookupError):
        _client().delete_draft("gone")
    assert seen["delete"] == []


def test_delete_draft_vanished_before_delete_is_lookup_error(monkeypatch):
    _fake_graph(monkeypatch, delete_status=404)
    with pytest.raises(LookupError):
        _client().delete_draft("d1")


def test_delete_draft_other_delete_error_raises(monkeypatch):
    _fake_graph(monkeypatch, delete_status=500)
    with pytest.raises(RuntimeError):
        _client().delete_draft("d1")


def test_delete_draft_shared_mailbox_targets_that_mailbox(monkeypatch):
    seen = _fake_graph(monkeypatch)
    _client().delete_draft("d1", from_address="shared@x.com", from_shared=True)
    assert seen["get"][0][0] == f"{GRAPH}/users/shared@x.com/messages/d1"
    assert seen["delete"] == [f"{GRAPH}/users/shared@x.com/messages/d1"]


def test_delete_draft_alias_uses_primary_mailbox(monkeypatch):
    seen = _fake_graph(monkeypatch)
    _client().delete_draft("d1", from_address="ben@drolet.ai")
    assert seen["delete"] == [f"{GRAPH}/me/messages/d1"]


def test_delete_draft_quotes_id_in_both_urls(monkeypatch):
    seen = _fake_graph(monkeypatch)
    _client().delete_draft("AA/B+C=")
    assert seen["get"][0][0] == f"{GRAPH}/me/messages/AA%2FB%2BC%3D"
    assert seen["delete"] == [f"{GRAPH}/me/messages/AA%2FB%2BC%3D"]
