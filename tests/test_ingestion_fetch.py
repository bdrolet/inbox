import requests

from services import ingestion


class _RaisingClient:
    def get_email_details(self, message_id):
        raise requests.exceptions.HTTPError("boom")


class _OkClient:
    def get_email_details(self, message_id):
        return "the-email"


def test_fetch_returns_none_on_graph_error():
    assert ingestion.fetch("m1", _RaisingClient()) is None


def test_fetch_passes_through_success():
    assert ingestion.fetch("m1", _OkClient()) == "the-email"


def test_normalize_carries_to_and_cc():
    from clients.azure.email import Email

    email = Email(
        {
            "id": "g1",
            "subject": "s",
            "from": {"emailAddress": {"name": "Alice", "address": "a@b.com"}},
            "toRecipients": [
                {"emailAddress": {"name": "Ben", "address": "ben@drolet.cloud"}},
                {"emailAddress": {"name": "NoAddr"}},
            ],
            "ccRecipients": [{"emailAddress": {"address": "team@example.com"}}],
            "body": {"contentType": "text", "content": "hi"},
            "receivedDateTime": "2026-07-15T12:00:00Z",
        }
    )
    msg = ingestion.normalize(email)
    assert msg["to"] == ["ben@drolet.cloud"]
    assert msg["cc"] == ["team@example.com"]
