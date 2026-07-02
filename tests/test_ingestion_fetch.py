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
