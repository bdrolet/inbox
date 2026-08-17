from types import SimpleNamespace

from clients.azure import graph_email_client as gec


class _V:
    def __init__(self, n, state="ENABLED"):
        self.name = f"projects/p/secrets/msal-token-cache/versions/{n}"
        self.state = SimpleNamespace(name=state)


class _Client:
    def __init__(self, versions):
        self.versions, self.destroyed = versions, []

    def list_secret_versions(self, request):
        return list(self.versions)  # Secret Manager returns newest first

    def destroy_secret_version(self, request):
        self.destroyed.append(request["name"])


def test_prune_keeps_newest_enabled_and_destroys_rest():
    c = _Client([_V(9), _V(8), _V(7, "DESTROYED"), _V(6), _V(5), _V(4)])
    assert gec.prune_secret_versions(c, "projects/p/secrets/msal-token-cache", keep=3) == 2
    assert c.destroyed == [
        "projects/p/secrets/msal-token-cache/versions/5",
        "projects/p/secrets/msal-token-cache/versions/4",
    ]


def test_prune_never_raises():
    class _Boom:
        def list_secret_versions(self, request):
            raise RuntimeError("perm")

    assert gec.prune_secret_versions(_Boom(), "projects/p/secrets/x", keep=3) == 0


def test_prune_malformed_keep_env_never_raises(monkeypatch):
    monkeypatch.setenv("MSAL_CACHE_KEEP_VERSIONS", "not-a-number")
    c = _Client([_V(9), _V(8), _V(7)])
    assert gec.prune_secret_versions(c, "projects/p/secrets/msal-token-cache") == 0


def test_prune_keep_zero_is_clamped_to_one(monkeypatch):
    """keep=0 would otherwise destroy every enabled version, including the one the
    writer just added — leaving `versions/latest` unreadable for every service."""
    monkeypatch.setenv("MSAL_CACHE_KEEP_VERSIONS", "0")
    c = _Client([_V(9), _V(8), _V(7)])
    assert gec.prune_secret_versions(c, "projects/p/secrets/msal-token-cache") == 2
    assert c.destroyed == [
        "projects/p/secrets/msal-token-cache/versions/8",
        "projects/p/secrets/msal-token-cache/versions/7",
    ]


def test_prune_never_destroys_protected_version():
    c = _Client([_V(9), _V(8), _V(7), _V(6)])
    protected = "projects/p/secrets/msal-token-cache/versions/6"
    assert gec.prune_secret_versions(c, "projects/p/secrets/x", keep=3, protect=protected) == 0
    assert c.destroyed == []


def test_prune_continues_after_a_failed_destroy():
    """One 429 / already-destroyed version must not abort the remaining destroys."""

    class _FlakyClient(_Client):
        def destroy_secret_version(self, request):
            if request["name"].endswith("/5"):
                raise RuntimeError("RESOURCE_EXHAUSTED")
            super().destroy_secret_version(request)

    c = _FlakyClient([_V(9), _V(8), _V(7), _V(6), _V(5), _V(4)])
    assert gec.prune_secret_versions(c, "projects/p/secrets/x", keep=3) == 2
    assert c.destroyed == [
        "projects/p/secrets/msal-token-cache/versions/6",
        "projects/p/secrets/msal-token-cache/versions/4",
    ]


def test_save_cache_prune_failure_never_breaks_authentication(monkeypatch):
    """The seam: _save_cache_to_secret_manager -> prune. A prune blowing up (or
    trying to destroy the version just written) must not propagate to the caller,
    because that would fail headless authentication."""
    from google.cloud import secretmanager

    new_version = _V(10)

    class _SaveClient(_Client):
        def add_secret_version(self, request):
            self.added = request
            return new_version

        def destroy_secret_version(self, request):
            raise RuntimeError("permission denied")

    client = _SaveClient([new_version, _V(9), _V(8), _V(7), _V(6)])
    monkeypatch.setattr(secretmanager, "SecretManagerServiceClient", lambda: client)
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.delenv("MSAL_CACHE_KEEP_VERSIONS", raising=False)

    obj = object.__new__(gec.GraphEmailClient)
    obj.app = SimpleNamespace(
        token_cache=SimpleNamespace(has_state_changed=True, serialize=lambda: "{}")
    )

    obj._save_cache_to_secret_manager()  # must not raise

    assert client.added["parent"] == "projects/p/secrets/msal-token-cache"
    assert client.destroyed == []  # every destroy failed, and none propagated


def test_save_cache_protects_the_version_it_just_wrote(monkeypatch):
    from google.cloud import secretmanager

    new_version = _V(10)

    class _SaveClient(_Client):
        def add_secret_version(self, request):
            return new_version

    # Secret Manager lists newest first, but assert the guard holds even if the
    # freshly added version lands outside the keep window.
    client = _SaveClient([_V(9), _V(8), _V(7), new_version])
    monkeypatch.setattr(secretmanager, "SecretManagerServiceClient", lambda: client)
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.delenv("MSAL_CACHE_KEEP_VERSIONS", raising=False)

    obj = object.__new__(gec.GraphEmailClient)
    obj.app = SimpleNamespace(
        token_cache=SimpleNamespace(has_state_changed=True, serialize=lambda: "{}")
    )

    obj._save_cache_to_secret_manager()

    assert new_version.name not in client.destroyed
