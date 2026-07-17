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
        {"external_id": "IMMUT-1"} if mid == "550e8400-e29b-41d4-a716-446655440000" else None
    )
    monkeypatch.setitem(sys.modules, "repo.messages", fake_repo)

    import repo

    monkeypatch.setattr(repo, "messages", fake_repo, raising=False)

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
    r = client.get("/r/550e8400-e29b-41d4-a716-446655440000")
    assert r.status_code == 302
    assert r.headers["location"] == "https://outlook.example/msg"


def test_unknown_uuid_404(client):
    # a well-formed UUID that the repo doesn't know -> exercises the None-row 404
    # branch (distinct from the malformed-uuid validation 404 below)
    r = client.get("/r/11111111-1111-1111-1111-111111111111")
    assert r.status_code == 404


def test_malformed_uuid_404(client):
    r = client.get("/r/not-a-uuid")
    assert r.status_code == 404
