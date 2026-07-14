import sys
import types

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    # Stub the heavy modules the router imports at load time.
    fake_db = types.ModuleType("clients.db")
    fake_db.get_conn = lambda: _FakeConnCtx()
    monkeypatch.setitem(sys.modules, "clients.db", fake_db)

    fake_graph = types.ModuleType("clients.graph")
    fake_graph.get_graph_client = lambda: _FakeGraph()
    monkeypatch.setitem(sys.modules, "clients.graph", fake_graph)

    fake_repo = types.ModuleType("repo.messages")
    fake_repo.get = lambda conn, mid: (
        {"external_id": "IMMUT-1"} if mid == "known" else None
    )
    monkeypatch.setitem(sys.modules, "repo.messages", fake_repo)

    import importlib

    import api.routers.redirect as redirect

    importlib.reload(redirect)
    app = FastAPI()
    app.include_router(redirect.router)
    return TestClient(app, follow_redirects=False)


class _FakeConnCtx:
    def __enter__(self):
        return object()

    def __exit__(self, *a):
        return False


class _FakeGraph:
    def get_web_link(self, external_id):
        return "https://outlook.example/msg" if external_id == "IMMUT-1" else None


def test_known_uuid_redirects(client):
    r = client.get("/r/known")
    assert r.status_code == 302
    assert r.headers["location"] == "https://outlook.example/msg"


def test_unknown_uuid_404(client):
    r = client.get("/r/missing")
    assert r.status_code == 404
