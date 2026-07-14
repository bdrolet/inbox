import clients.azure.graph_email_client as graph_email_client
from clients.azure.graph_email_client import GraphEmailClient


def _client():
    c = GraphEmailClient.__new__(GraphEmailClient)
    c.access_token = "tok"
    c.graph_endpoint = "https://graph.microsoft.com/v1.0"
    return c


def test_get_headers_default_has_no_prefer():
    h = _client().get_headers()
    assert "Prefer" not in h
    assert h["Authorization"] == "Bearer tok"


def test_get_headers_immutable_sets_prefer():
    h = _client().get_headers(immutable=True)
    assert h["Prefer"] == 'IdType="ImmutableId"'


class _Resp:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.text = text

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(str(self.status_code))


def test_tag_message_sends_immutable_id_header(monkeypatch):
    seen = {}

    def fake_patch(url, headers=None, json=None):
        seen["headers"] = headers
        return _Resp()

    monkeypatch.setattr(graph_email_client.requests, "patch", fake_patch)
    ok = _client().tag_message("msg-immutable-id", ["reply_required"])
    assert ok is True
    assert seen["headers"]["Prefer"] == 'IdType="ImmutableId"'


def test_get_attachments_sends_immutable_id_header(monkeypatch):
    seen = {}

    def fake_get(url, headers=None):
        seen["headers"] = headers
        return _Resp(json_data={"value": []})

    monkeypatch.setattr(graph_email_client.requests, "get", fake_get)
    _client().get_attachments("msg-immutable-id")
    assert seen["headers"]["Prefer"] == 'IdType="ImmutableId"'
