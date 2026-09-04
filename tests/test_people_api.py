import pytest
import requests

from clients import people_api


class Resp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._p = payload or {}

    def json(self):
        return self._p

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("PEOPLE_API_URL", "https://people.example")
    monkeypatch.setenv("PEOPLE_API_TOKEN", "t0k")


def test_hit_returns_the_four_keys(monkeypatch, env):
    seen = {}

    def fake_get(url, headers, timeout):
        seen.update(url=url, headers=headers, timeout=timeout)
        return Resp(
            200,
            {
                "email": "a@x.com",
                "message_count": 3,
                "my_response_count": 1,
                "relationship_label": "family",
                "notes": None,
                "eligible": True,
            },
        )

    monkeypatch.setattr(people_api.requests, "get", fake_get)
    out = people_api.get_person("A@X.com")
    assert out == {
        "message_count": 3,
        "my_response_count": 1,
        "relationship_label": "family",
        "notes": None,
    }
    assert seen["url"] == "https://people.example/people/a@x.com"
    assert seen["headers"] == {"Authorization": "Bearer t0k"} and seen["timeout"] == 2


def test_404_is_none(monkeypatch, env):
    monkeypatch.setattr(people_api.requests, "get", lambda *a, **k: Resp(404))
    assert people_api.get_person("a@x.com") is None


def test_timeout_is_none(monkeypatch, env):
    def boom(*a, **k):
        raise requests.Timeout()

    monkeypatch.setattr(people_api.requests, "get", boom)
    assert people_api.get_person("a@x.com") is None


def test_500_is_none(monkeypatch, env):
    monkeypatch.setattr(people_api.requests, "get", lambda *a, **k: Resp(500))
    assert people_api.get_person("a@x.com") is None


def test_unset_url_is_none_without_calling(monkeypatch):
    monkeypatch.delenv("PEOPLE_API_URL", raising=False)

    def fail(*a, **k):
        raise AssertionError("must not call")

    monkeypatch.setattr(people_api.requests, "get", fail)
    assert people_api.get_person("a@x.com") is None
