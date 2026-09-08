"""Unit tests for the self-healing renew Cloud Function.

The renew module is a standalone Cloud Function whose third-party imports
(functions_framework, msal, requests, google-cloud-secret-manager) are not part
of the CI test environment (which installs only requirements-dev.txt). Each unit
test monkeypatches the functions that would actually call those libraries, so we
stub any that aren't installed before loading the module straight from its file
path. When a real library IS available (e.g. locally) we use it, preserving
fidelity.
"""

import importlib
import importlib.util
import sys
import types
from pathlib import Path

import pytest


def _ensure_module(name, **attrs):
    """Use the real module if importable, else register a stub with `attrs`."""
    try:
        importlib.import_module(name)
    except ImportError:
        mod = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(mod, key, value)
        sys.modules[name] = mod


_ensure_module("functions_framework", http=lambda fn: fn)  # identity decorator
_ensure_module("msal")
_ensure_module(
    "requests",
    Response=type("Response", (), {}),  # used only in a type hint
    post=lambda *a, **k: None,  # overwritten per-test via monkeypatch.setattr
)

# google-cloud-secret-manager + api-core: build the package chain only if absent.
try:
    from google.api_core import exceptions as _gac_exceptions  # noqa: F401
    from google.cloud import secretmanager as _gc_secretmanager  # noqa: F401
except ImportError:
    _google = sys.modules.setdefault("google", types.ModuleType("google"))
    _cloud = sys.modules.setdefault("google.cloud", types.ModuleType("google.cloud"))
    _google.cloud = _cloud
    _cloud.secretmanager = sys.modules.setdefault(
        "google.cloud.secretmanager", types.ModuleType("google.cloud.secretmanager")
    )
    _api_core = sys.modules.setdefault("google.api_core", types.ModuleType("google.api_core"))
    _google.api_core = _api_core
    _exc = sys.modules.setdefault(
        "google.api_core.exceptions", types.ModuleType("google.api_core.exceptions")
    )
    _exc.NotFound = type("NotFound", (Exception,), {})
    _api_core.exceptions = _exc

_RENEW_PATH = Path(__file__).resolve().parents[1] / "functions" / "renew" / "main.py"
_spec = importlib.util.spec_from_file_location("renew_main", _RENEW_PATH)
renew_main = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(renew_main)

INBOX = {
    "resource": "me/mailFolders/inbox/messages",
    "client_state": "inbox-webhook",
    "secret_name": "graph-subscription-id",
}
SENT = {
    "resource": "me/mailFolders/sentitems/messages",
    "client_state": "inbox-webhook-sent",
    "secret_name": "graph-sent-subscription-id",
}


class _Resp:
    """Minimal stand-in for requests.Response."""

    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")


def _fail(*_a, **_k):
    raise AssertionError("must not be called")


def test_renew_patches_existing_subscription(monkeypatch):
    calls = {}
    monkeypatch.setattr(
        renew_main,
        "_patch_subscription",
        lambda sid, tok: _Resp(200, {"id": sid, "expirationDateTime": "2026-07-01T00:00:00Z"}),
    )
    monkeypatch.setattr(
        renew_main, "_register_subscription", lambda sub, tok: calls.setdefault("registered", True)
    )
    monkeypatch.setattr(
        renew_main, "_save_subscription_id", lambda sub, sid: calls.setdefault("saved", sid)
    )

    result = renew_main._renew_or_register(INBOX, "sub-123", "tok")

    assert result["id"] == "sub-123"
    assert "registered" not in calls  # must NOT re-register on a healthy subscription
    assert "saved" not in calls  # secret must NOT be rewritten on a plain renewal


def test_renew_registers_replacement_on_404(monkeypatch):
    saved = {}
    monkeypatch.setattr(
        renew_main, "_patch_subscription", lambda sid, tok: _Resp(404, text="ResourceNotFound")
    )
    monkeypatch.setattr(
        renew_main,
        "_register_subscription",
        lambda sub, tok: {"id": "sub-new", "expirationDateTime": "2026-07-01T00:00:00Z"},
    )
    monkeypatch.setattr(renew_main, "_save_subscription_id", lambda sub, sid: saved.update(id=sid))

    result = renew_main._renew_or_register(INBOX, "sub-dead", "tok")

    assert result["id"] == "sub-new"
    assert saved["id"] == "sub-new"  # new ID persisted for the next run


def test_register_when_no_subscription_id(monkeypatch):
    saved = {}
    monkeypatch.setattr(renew_main, "_patch_subscription", _fail)  # patch must never run
    monkeypatch.setattr(
        renew_main,
        "_register_subscription",
        lambda sub, tok: {"id": "sub-boot", "expirationDateTime": "2026-07-01T00:00:00Z"},
    )
    monkeypatch.setattr(renew_main, "_save_subscription_id", lambda sub, sid: saved.update(id=sid))

    result = renew_main._renew_or_register(INBOX, "", "tok")

    assert result["id"] == "sub-boot"
    assert saved["id"] == "sub-boot"


def test_non_404_error_does_not_register(monkeypatch):
    monkeypatch.setattr(
        renew_main, "_patch_subscription", lambda sid, tok: _Resp(401, text="Unauthorized")
    )
    monkeypatch.setattr(renew_main, "_register_subscription", _fail)  # never register on a 401
    monkeypatch.setattr(renew_main, "_save_subscription_id", _fail)

    with pytest.raises(RuntimeError):
        renew_main._renew_or_register(INBOX, "sub-123", "tok")


def test_register_reuses_existing_matching_subscription(monkeypatch):
    existing_sub = {
        "id": "sub-existing",
        "notificationUrl": "https://webhook.example.com",
        "resource": "me/mailFolders/inbox/messages",
    }
    monkeypatch.setenv("WEBHOOK_URL", "https://webhook.example.com")
    monkeypatch.setattr(renew_main, "_list_subscriptions", lambda tok: [existing_sub])
    monkeypatch.setattr(renew_main, "_create_subscription", _fail)

    result = renew_main._register_subscription(INBOX, "tok")

    assert result["id"] == "sub-existing"


def test_register_creates_when_none_match(monkeypatch):
    monkeypatch.setenv("WEBHOOK_URL", "https://webhook.example.com")
    monkeypatch.setattr(renew_main, "_list_subscriptions", lambda tok: [])
    monkeypatch.setattr(renew_main, "_create_subscription", lambda sub, tok: {"id": "sub-created"})

    result = renew_main._register_subscription(INBOX, "tok")

    assert result["id"] == "sub-created"


def test_register_matches_on_resource_not_just_url(monkeypatch):
    monkeypatch.setenv("WEBHOOK_URL", "https://webhook.example.com")
    inbox_sub = {
        "id": "sub-inbox",
        "notificationUrl": "https://webhook.example.com",
        "resource": INBOX["resource"],
    }
    monkeypatch.setattr(renew_main, "_list_subscriptions", lambda tok: [inbox_sub])
    monkeypatch.setattr(
        renew_main, "_create_subscription", lambda sub, tok: {"id": "sub-created", **sub}
    )
    assert renew_main._register_subscription(SENT, "tok")["id"] == "sub-created"


def test_subscriptions_from_env(monkeypatch):
    monkeypatch.setenv("SUBSCRIPTION_SECRET_NAME", "graph-subscription-id")
    monkeypatch.setenv("SENT_SUBSCRIPTION_SECRET_NAME", "graph-sent-subscription-id")
    monkeypatch.setenv("WEBHOOK_CLIENT_STATE", "inbox-webhook")
    monkeypatch.setenv("WEBHOOK_CLIENT_STATE_SENT", "inbox-webhook-sent")
    subs = renew_main._subscriptions()
    assert [s["resource"] for s in subs] == [INBOX["resource"], SENT["resource"]]
    assert (
        subs[1]["client_state"] == "inbox-webhook-sent"
        and subs[1]["secret_name"] == "graph-sent-subscription-id"
    )


def test_create_subscription_sends_immutable_id_header(monkeypatch):
    seen_headers = {}

    def fake_post(url, json=None, headers=None):
        seen_headers.update(headers)
        return _Resp(201, {"id": "s"})

    monkeypatch.setattr(renew_main.requests, "post", fake_post)
    monkeypatch.setenv("WEBHOOK_URL", "https://webhook.example.com")

    result = renew_main._create_subscription(INBOX, "tok")

    assert result["id"] == "s"
    assert seen_headers["Prefer"] == 'IdType="ImmutableId"'
    assert seen_headers["Authorization"] == "Bearer tok"


def test_renew_handles_each_subscription(monkeypatch):
    seen = []
    monkeypatch.setattr(renew_main, "_get_access_token", lambda: "tok")
    monkeypatch.setattr(renew_main, "_subscriptions", lambda: [INBOX, SENT])
    monkeypatch.setattr(
        renew_main, "_load_subscription_id", lambda sub: "id-" + sub["client_state"]
    )
    monkeypatch.setattr(
        renew_main,
        "_renew_or_register",
        lambda sub, sid, tok: seen.append((sub["resource"], sid)) or {"id": sid},
    )
    body, status, _ = renew_main.renew(None)
    assert status == 200 and len(seen) == 2
