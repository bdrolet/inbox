import importlib
import importlib.util
import sys
import types
from pathlib import Path

import pytest


def _ensure(name, **attrs):
    try:
        importlib.import_module(name)
    except ImportError:
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules[name] = mod


_ensure("functions_framework", http=lambda fn: fn)
try:
    import google.cloud.pubsub_v1  # noqa: F401
except ImportError:
    g = sys.modules.setdefault("google", types.ModuleType("google"))
    c = sys.modules.setdefault("google.cloud", types.ModuleType("google.cloud"))
    g.cloud = c
    pv1 = types.ModuleType("google.cloud.pubsub_v1")
    pv1.PublisherClient = object
    c.pubsub_v1 = pv1
    sys.modules["google.cloud.pubsub_v1"] = pv1

_PATH = Path(__file__).resolve().parents[1] / "functions" / "webhook" / "main.py"
_spec = importlib.util.spec_from_file_location("webhook_main", _PATH)
webhook_main = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(webhook_main)


class Fut:
    def result(self, timeout=None):
        return "id"


class Pub:
    def __init__(self):
        self.calls = []

    def publish(self, topic, data, **attrs):
        self.calls.append((topic, attrs))
        return Fut()


class Req:
    path = "/"
    method = "POST"
    args = {}
    headers = {}

    def __init__(self, body):
        self._b = body

    def get_json(self, silent=True):
        return self._b


@pytest.fixture
def pub(monkeypatch):
    p = Pub()
    monkeypatch.setattr(webhook_main, "_publisher_client", lambda: (p, "t-messages", "t-labels"))
    monkeypatch.setattr(webhook_main, "_flush", lambda: None)
    monkeypatch.setenv("WEBHOOK_CLIENT_STATE", "inbox-webhook")
    monkeypatch.setenv("WEBHOOK_CLIENT_STATE_SENT", "inbox-webhook-sent")
    return p


def _n(state):
    return {"value": [{"changeType": "created", "clientState": state, "resourceData": {"id": "x"}}]}


def test_inbox_state_gets_folder_inbox(pub):
    webhook_main.webhook(Req(_n("inbox-webhook")))
    assert pub.calls[0][1]["folder"] == "inbox"


def test_sent_state_gets_folder_sentitems(pub):
    webhook_main.webhook(Req(_n("inbox-webhook-sent")))
    assert pub.calls[0][1]["folder"] == "sentitems"


def test_unknown_state_rejected(pub):
    webhook_main.webhook(Req(_n("nope")))
    assert pub.calls == []
