import pytest

import clients.azure.graph_email_client as graph_email_client
from clients.azure.graph_email_client import GraphEmailClient

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


@pytest.fixture
def calls(monkeypatch):
    seen = {"post": [], "get": []}

    def fake_post(url, headers=None, json=None):
        seen["post"].append((url, json))
        return _Resp(202 if url.endswith(("/reply", "/replyAll")) else 201, {"id": "d1"})

    def fake_get(url, headers=None, params=None):
        seen["get"].append((url, params))
        return _Resp(200, {"id": "d1", "webLink": "https://outlook/d1"})

    monkeypatch.setattr(graph_email_client.requests, "post", fake_post)
    monkeypatch.setattr(graph_email_client.requests, "get", fake_get)
    return seen


def test_reply_send_posts_comment_to_reply_action(calls):
    assert _client().reply("m1", comment="Sounds great") is None
    assert calls["post"] == [(f"{GRAPH}/me/messages/m1/reply", {"comment": "Sounds great"})]
    assert calls["get"] == []


def test_reply_all_uses_reply_all_action(calls):
    _client().reply("m1", comment="hi", reply_all=True)
    assert calls["post"][0][0] == f"{GRAPH}/me/messages/m1/replyAll"


def test_reply_alias_stamps_from_on_message(calls):
    _client().reply("m1", comment="hi", from_address="ben@drolet.ai")
    url, payload = calls["post"][0]
    assert url == f"{GRAPH}/me/messages/m1/reply"
    assert payload["message"] == {"from": {"emailAddress": {"address": "ben@drolet.ai"}}}


def test_reply_shared_mailbox_targets_that_mailbox(calls):
    _client().reply("m1", comment="hi", from_address="shared@x.com", from_shared=True)
    url, payload = calls["post"][0]
    assert url == f"{GRAPH}/users/shared@x.com/messages/m1/reply"
    assert "message" not in payload


def test_reply_draft_uses_create_reply_and_returns_web_link(calls):
    draft = _client().reply("m1", comment="hi", send=False)
    assert calls["post"][0][0] == f"{GRAPH}/me/messages/m1/createReply"
    assert calls["get"][0][0] == f"{GRAPH}/me/messages/d1"
    assert draft == {"id": "d1", "webLink": "https://outlook/d1"}


def test_reply_all_draft_uses_create_reply_all(calls):
    _client().reply("m1", comment="hi", reply_all=True, send=False)
    assert calls["post"][0][0] == f"{GRAPH}/me/messages/m1/createReplyAll"


def test_reply_encodes_message_id_in_graph_path(calls):
    _client().reply("AA/BB+C=", comment="hi")
    assert calls["post"][0][0] == f"{GRAPH}/me/messages/AA%2FBB%2BC%3D/reply"


def test_reply_missing_message_raises_lookup_error(monkeypatch):
    monkeypatch.setattr(
        graph_email_client.requests, "post", lambda url, headers=None, json=None: _Resp(404)
    )
    with pytest.raises(LookupError):
        _client().reply("gone", comment="hi")
