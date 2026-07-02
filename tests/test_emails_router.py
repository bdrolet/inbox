import pytest
import requests
from fastapi.testclient import TestClient

import services.fetching as fetching
from api.main import app
from clients.azure.email import Email

client = TestClient(app)


@pytest.fixture(autouse=True)
def _no_auth(monkeypatch):
    """_verify_token reads SEARCH_TOKEN per request — unset it so tests skip auth."""
    monkeypatch.delenv("SEARCH_TOKEN", raising=False)


def _http_error(status: int) -> requests.exceptions.HTTPError:
    resp = requests.Response()
    resp.status_code = status
    resp._content = b"graph says no"
    return requests.exceptions.HTTPError(str(status), response=resp)


def _single() -> fetching.FetchedEmail:
    return fetching.FetchedEmail(email=Email({"id": "m1", "subject": "hello"}))


def _group() -> fetching.FetchedEmail:
    posts = [
        {
            "id": "p1",
            "threadId": "t1",
            "receivedDateTime": "2026-07-01T10:00:00Z",
            "body": {"contentType": "html", "content": "<p>hi</p>"},
            "from": {"emailAddress": {"name": "Ann", "address": "ann@x.com"}},
            "hasAttachments": False,
        }
    ]
    return fetching.FetchedEmail(
        email=Email({"id": "c1", "subject": "Lunch"}), posts=posts
    )


def test_get_email_defaults_to_me(monkeypatch):
    seen = {}

    def fake(message_id, mailbox="me"):
        seen["args"] = (message_id, mailbox)
        return _single()

    monkeypatch.setattr(fetching, "fetch_email", fake)
    resp = client.get("/emails/m1")
    assert resp.status_code == 200
    assert seen["args"] == ("m1", "me")
    body = resp.json()
    assert body["subject"] == "hello"
    assert body["posts"] is None


def test_get_email_passes_mailbox(monkeypatch):
    seen = {}

    def fake(message_id, mailbox="me"):
        seen["mailbox"] = mailbox
        return _single()

    monkeypatch.setattr(fetching, "fetch_email", fake)
    resp = client.get("/emails/m1", params={"mailbox": "team@x.com"})
    assert resp.status_code == 200
    assert seen["mailbox"] == "team@x.com"


def test_get_email_group_returns_posts(monkeypatch):
    monkeypatch.setattr(fetching, "fetch_email", lambda mid, mailbox="me": _group())
    resp = client.get("/emails/c1", params={"mailbox": "group:eng@x.com"})
    assert resp.status_code == 200
    posts = resp.json()["posts"]
    assert len(posts) == 1
    assert posts[0]["id"] == "p1"
    assert posts[0]["thread_id"] == "t1"
    assert posts[0]["sender_email"] == "ann@x.com"
    assert posts[0]["body"] == "<p>hi</p>"
    assert posts[0]["body_type"] == "html"


def test_get_email_none_is_404(monkeypatch):
    monkeypatch.setattr(fetching, "fetch_email", lambda mid, mailbox="me": None)
    assert client.get("/emails/gone").status_code == 404


def test_get_email_unknown_group_is_404(monkeypatch):
    def fake(mid, mailbox="me"):
        raise LookupError("unknown group: nope@x.com")

    monkeypatch.setattr(fetching, "fetch_email", fake)
    resp = client.get("/emails/c1", params={"mailbox": "group:nope@x.com"})
    assert resp.status_code == 404
    assert "unknown group" in resp.json()["detail"]


def test_get_email_403_maps_to_403(monkeypatch):
    def fake(mid, mailbox="me"):
        raise _http_error(403)

    monkeypatch.setattr(fetching, "fetch_email", fake)
    assert client.get("/emails/m1", params={"mailbox": "team@x.com"}).status_code == 403


def test_get_email_500_maps_to_502(monkeypatch):
    def fake(mid, mailbox="me"):
        raise _http_error(500)

    monkeypatch.setattr(fetching, "fetch_email", fake)
    assert client.get("/emails/m1").status_code == 502


def test_get_email_keyerror_is_not_swallowed_as_404(monkeypatch):
    def fake(mid, mailbox="me"):
        raise KeyError("threadId")

    monkeypatch.setattr(fetching, "fetch_email", fake)
    with pytest.raises(KeyError):
        client.get("/emails/m1")


def test_get_email_auth_failure_maps_to_503(monkeypatch):
    def fake(mid, mailbox="me"):
        raise RuntimeError("Graph API headless authentication failed")

    monkeypatch.setattr(fetching, "fetch_email", fake)
    assert client.get("/emails/m1").status_code == 503


def test_get_attachments_passes_mailbox_and_post_id(monkeypatch):
    def fake(message_id, mailbox="me"):
        return [{"id": "a1", "name": "f.pdf", "postId": "p1"}]

    monkeypatch.setattr(fetching, "fetch_attachments", fake)
    resp = client.get("/emails/c1/attachments", params={"mailbox": "group:eng@x.com"})
    assert resp.status_code == 200
    att = resp.json()["attachments"][0]
    assert att["post_id"] == "p1"


def test_get_attachments_no_post_id_for_plain_messages(monkeypatch):
    monkeypatch.setattr(
        fetching, "fetch_attachments", lambda mid, mailbox="me": [{"id": "a1", "name": "f.pdf"}]
    )
    att = client.get("/emails/m1/attachments").json()["attachments"][0]
    assert att["post_id"] is None


def test_get_attachments_lookup_error_is_404(monkeypatch):
    def fake(mid, mailbox="me"):
        raise LookupError("message not found")

    monkeypatch.setattr(fetching, "fetch_attachments", fake)
    assert client.get("/emails/gone/attachments").status_code == 404
