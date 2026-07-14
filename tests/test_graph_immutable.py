from clients.azure.graph_email_client import GraphEmailClient


def _client():
    c = GraphEmailClient.__new__(GraphEmailClient)
    c.access_token = "tok"
    return c


def test_get_headers_default_has_no_prefer():
    h = _client().get_headers()
    assert "Prefer" not in h
    assert h["Authorization"] == "Bearer tok"


def test_get_headers_immutable_sets_prefer():
    h = _client().get_headers(immutable=True)
    assert h["Prefer"] == 'IdType="ImmutableId"'
