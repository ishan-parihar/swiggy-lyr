import time

from swiggy_lyr import session_state
from swiggy_lyr.exceptions import NotAuthenticatedError, TokenExpiredError
from swiggy_lyr.session_state import (
    clear_token,
    get_bearer_token,
    load_token,
    save_token,
    store_manual_token,
)


def _use_tmp_path(monkeypatch, tmp_path):
    path = tmp_path / "token.json"
    monkeypatch.setattr(session_state, "TOKEN_PATH", path)
    monkeypatch.setattr(session_state, "TOKEN_DIR", tmp_path)
    return path


def test_save_load_roundtrip(monkeypatch, tmp_path):
    _use_tmp_path(monkeypatch, tmp_path)
    save_token({"access_token": "abc"})
    payload = load_token()
    assert payload is not None
    assert payload["access_token"] == "abc"
    assert payload["mode"] == "oauth"
    assert payload.get("saved_at")


def test_manual_token_mode(monkeypatch, tmp_path):
    _use_tmp_path(monkeypatch, tmp_path)
    store_manual_token("  bearer-xyz  ")
    token, source = get_bearer_token()
    assert token == "bearer-xyz"
    assert source == "file"


def test_env_overrides_file(monkeypatch, tmp_path):
    _use_tmp_path(monkeypatch, tmp_path)
    store_manual_token("file-token")
    monkeypatch.setenv("SWIGGY_LYR_TOKEN", "env-token")
    token, source = get_bearer_token()
    assert (token, source) == ("env-token", "env")


def test_missing_token_raises(monkeypatch, tmp_path):
    _use_tmp_path(monkeypatch, tmp_path)
    monkeypatch.delenv("SWIGGY_LYR_TOKEN", raising=False)
    with pytest_raises(NotAuthenticatedError):
        get_bearer_token()


def test_expired_token_raises(monkeypatch, tmp_path):
    _use_tmp_path(monkeypatch, tmp_path)
    monkeypatch.delenv("SWIGGY_LYR_TOKEN", raising=False)
    save_token({"access_token": "old", "expires_at": time.time() - 10})
    with pytest_raises(TokenExpiredError):
        get_bearer_token()


def test_clear(monkeypatch, tmp_path):
    path = _use_tmp_path(monkeypatch, tmp_path)
    store_manual_token("x")
    assert clear_token() is True
    assert not path.exists()
    assert clear_token() is False


def pytest_raises(exc):
    import pytest

    return pytest.raises(exc)
