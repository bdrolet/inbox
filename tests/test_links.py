import services.links as links


def test_redirector_url_builds(monkeypatch):
    monkeypatch.setenv("REDIRECTOR_BASE_URL", "https://inbox-api.example.com/")
    assert links.redirector_url("abc-123") == "https://inbox-api.example.com/r/abc-123"


def test_redirector_url_none_when_unset(monkeypatch):
    monkeypatch.delenv("REDIRECTOR_BASE_URL", raising=False)
    assert links.redirector_url("abc-123") is None


def test_redirector_url_none_when_no_uuid(monkeypatch):
    monkeypatch.setenv("REDIRECTOR_BASE_URL", "https://x")
    assert links.redirector_url("") is None
