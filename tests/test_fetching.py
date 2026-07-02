import pytest
import requests

import services.fetching as fetching
from services.fetching import FetchedEmail, fetch_attachments, fetch_email


class FakeClient:
    def __init__(self):
        self.groups = [{"id": "g1", "display_name": "Eng", "mail": "eng@x.com"}]
        self.emails = {}  # (message_id, mailbox) -> Email-constructor dict
        self.attachments = {}  # (message_id, mailbox) -> list[dict]
        self.conversations = {}  # (group_id, conversation_id) -> convo dict
        self.post_attachments = {}  # (group_id, thread_id, post_id) -> list[dict]
        self.member_groups_error = None  # optional exception to raise

    def get_member_groups(self, raise_on_error=False):
        if self.member_groups_error is not None and raise_on_error:
            raise self.member_groups_error
        return self.groups

    def get_email_details(self, email_id, mailbox="me"):
        from clients.azure.email import Email

        data = self.emails.get((email_id, mailbox))
        return Email(data) if data else None

    def get_attachments(self, message_id, mailbox="me"):
        if (message_id, mailbox) not in self.attachments:
            raise LookupError("message not found")
        return self.attachments[(message_id, mailbox)]

    def get_group_conversation(self, group_id, conversation_id):
        return self.conversations.get((group_id, conversation_id))

    def get_group_post_attachments(self, group_id, thread_id, post_id):
        return self.post_attachments.get((group_id, thread_id, post_id), [])


@pytest.fixture
def client(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(fetching, "get_graph_client", lambda: fake)
    return fake


def _post(pid, when, body="hi", has_attachments=False):
    return {
        "id": pid,
        "threadId": "t1",
        "receivedDateTime": when,
        "body": {"contentType": "html", "content": body},
        "from": {"emailAddress": {"name": "Ann", "address": "ann@x.com"}},
        "hasAttachments": has_attachments,
    }


def test_fetch_email_default_me(client):
    client.emails[("m1", "me")] = {"id": "m1", "subject": "hello"}
    fetched = fetch_email("m1")
    assert isinstance(fetched, FetchedEmail)
    assert fetched.email.subject == "hello"
    assert fetched.posts is None


def test_fetch_email_shared_mailbox(client):
    client.emails[("m1", "team@x.com")] = {"id": "m1", "subject": "shared"}
    assert fetch_email("m1", mailbox="team@x.com").email.subject == "shared"


def test_fetch_email_not_found_returns_none(client):
    assert fetch_email("gone") is None


def test_fetch_email_group_by_mail_case_insensitive(client):
    client.conversations[("g1", "c1")] = {
        "topic": "Lunch",
        "lastDeliveredDateTime": "2026-07-01T12:00:00Z",
        "posts": [
            _post("p1", "2026-07-01T10:00:00Z"),
            _post("p2", "2026-07-01T12:00:00Z", body="latest"),
        ],
    }
    fetched = fetch_email("c1", mailbox="group:ENG@x.com")
    assert fetched.email.subject == "Lunch"
    assert fetched.email.body_content == "latest"  # mirrors latest post
    assert [p["id"] for p in fetched.posts] == ["p1", "p2"]  # oldest first
    assert fetched.email.web_link is None


def test_fetch_email_group_post_from_takes_precedence_over_sender(client):
    post = _post("p1", "2026-07-01T10:00:00Z")
    post["sender"] = {"emailAddress": {"name": "Delegate", "address": "delegate@x.com"}}
    client.conversations[("g1", "c1")] = {
        "topic": "T",
        "lastDeliveredDateTime": None,
        "posts": [post],
    }
    fetched = fetch_email("c1", mailbox="group:eng@x.com")
    assert fetched.email.from_email == "ann@x.com"  # `from` wins over `sender`


def test_fetch_email_group_by_id(client):
    client.conversations[("g1", "c1")] = {"topic": "T", "lastDeliveredDateTime": None, "posts": []}
    assert fetch_email("c1", mailbox="group:g1").email.subject == "T"


def test_fetch_email_unknown_group_raises(client):
    with pytest.raises(LookupError, match="unknown group"):
        fetch_email("c1", mailbox="group:nobody@x.com")


def test_fetch_email_group_conversation_not_found(client):
    assert fetch_email("gone", mailbox="group:eng@x.com") is None


def test_fetch_attachments_mailbox_passthrough(client):
    client.attachments[("m1", "team@x.com")] = [{"id": "a1"}]
    assert fetch_attachments("m1", mailbox="team@x.com") == [{"id": "a1"}]


def test_fetch_attachments_group_aggregates_with_post_id(client):
    client.conversations[("g1", "c1")] = {
        "topic": "T",
        "lastDeliveredDateTime": None,
        "posts": [
            _post("p1", "2026-07-01T10:00:00Z", has_attachments=True),
            _post("p2", "2026-07-01T11:00:00Z"),
        ],
    }
    original = {"id": "a1", "name": "f.pdf"}
    client.post_attachments[("g1", "t1", "p1")] = [original]
    atts = fetch_attachments("c1", mailbox="group:eng@x.com")
    assert atts == [{"id": "a1", "name": "f.pdf", "postId": "p1"}]
    assert original == {"id": "a1", "name": "f.pdf"}  # client's dict not mutated


def test_fetch_attachments_group_conversation_not_found(client):
    with pytest.raises(LookupError):
        fetch_attachments("gone", mailbox="group:eng@x.com")


def test_fetch_email_group_resolution_graph_error_propagates(client):
    resp = requests.Response()
    resp.status_code = 403
    client.member_groups_error = requests.exceptions.HTTPError("403", response=resp)
    with pytest.raises(requests.exceptions.HTTPError):
        fetch_email("c1", mailbox="group:eng@x.com")


def test_fetch_email_empty_group_label_raises(client):
    with pytest.raises(LookupError, match="unknown group"):
        fetch_email("c1", mailbox="group:")


def test_fetch_email_blank_group_label_raises(client):
    with pytest.raises(LookupError, match="unknown group"):
        fetch_email("c1", mailbox="group:  ")


def test_fetch_attachments_group_posts_sorted_oldest_first(client):
    client.conversations[("g1", "c1")] = {
        "topic": "T",
        "lastDeliveredDateTime": None,
        "posts": [
            _post("p2", "2026-07-01T11:00:00Z", has_attachments=True),
            _post("p1", "2026-07-01T10:00:00Z", has_attachments=True),
        ],
    }
    client.post_attachments[("g1", "t1", "p1")] = [{"id": "a1"}]
    client.post_attachments[("g1", "t1", "p2")] = [{"id": "a2"}]
    atts = fetch_attachments("c1", mailbox="group:eng@x.com")
    assert [a["postId"] for a in atts] == ["p1", "p2"]
