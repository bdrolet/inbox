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
