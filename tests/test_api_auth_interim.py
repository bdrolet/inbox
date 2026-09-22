import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from api.routers import emails, search


@pytest.mark.parametrize("verify", [search._verify_token, emails._verify_token])
def test_unset_token_on_cloud_run_fails_closed(monkeypatch, verify):
    monkeypatch.delenv("SEARCH_TOKEN", raising=False)
    monkeypatch.setenv("K_SERVICE", "inbox-api")
    with pytest.raises(HTTPException) as exc:
        verify(None)
    assert exc.value.status_code == 503


@pytest.mark.parametrize("verify", [search._verify_token, emails._verify_token])
def test_unset_token_off_cloud_run_is_open(monkeypatch, verify):
    monkeypatch.delenv("SEARCH_TOKEN", raising=False)
    monkeypatch.delenv("K_SERVICE", raising=False)
    assert verify(None) is None


@pytest.mark.parametrize("verify", [search._verify_token, emails._verify_token])
def test_wrong_token_is_401(monkeypatch, verify):
    monkeypatch.setenv("SEARCH_TOKEN", "s3cret")
    with pytest.raises(HTTPException) as exc:
        verify(HTTPAuthorizationCredentials(scheme="Bearer", credentials="nope"))
    assert exc.value.status_code == 401
