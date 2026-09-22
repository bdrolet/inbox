from fastapi.testclient import TestClient


def test_redirect_app_serves_only_the_redirector():
    from api.redirect_app import app

    assert set(app.openapi()["paths"]) == {"/r/{message_uuid}"}


def test_main_app_no_longer_routes_the_redirector():
    from api.main import app

    assert "/r/{message_uuid}" not in app.openapi()["paths"]


def test_malformed_uuid_is_404_without_db():
    from api.redirect_app import app

    assert TestClient(app).get("/r/not-a-uuid").status_code == 404
