"""Auth-status mode matrix + session edge cases."""

import time

import pytest

from swiggy_lyr import session_state
from swiggy_lyr.authentication import get_auth_status, validate_session
from swiggy_lyr.exceptions import SessionStateError
from swiggy_lyr.session_state import (
    load_token,
    save_token,
    store_manual_token,
)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.delenv("SWIGGY_LYR_TOKEN", raising=False)
    monkeypatch.setattr(session_state, "TOKEN_PATH", tmp_path / "token.json")
    monkeypatch.setattr(session_state, "TOKEN_DIR", tmp_path)


def _days(payload_field):
    return payload_field


def test_status_nothing_at_all():
    st = get_auth_status()
    assert st == {
        "authenticated": False,
        "mode": None,
        "source": None,
        "expires_in_days": None,
    }
    assert validate_session() is False


def test_status_env_only(monkeypatch):
    monkeypatch.setenv("SWIGGY_LYR_TOKEN", "envtok")
    st = get_auth_status()
    assert st["authenticated"] is True
    assert st["source"] == "env"
    assert validate_session() is True


def test_status_whitespace_env_falls_through_to_missing(monkeypatch, capsys):
    monkeypatch.setenv("SWIGGY_LYR_TOKEN", "   ")
    st = get_auth_status()
    assert st["authenticated"] is False


def test_status_oauth_with_expiry_countdown():
    save_token({"access_token": "t", "expires_at": time.time() + 5 * 86400})
    st = get_auth_status()
    assert st["mode"] == "oauth"
    assert 4.9 <= st["expires_in_days"] <= 5.1


def test_status_expired_oauth_reports_zero_but_unauthenticated_via_get_token():
    save_token({"access_token": "t", "expires_at": time.time() - 100})
    # status reflects file presence/mode; get_bearer_token refuses expired
    st = get_auth_status()
    assert st["authenticated"] is False  # get_bearer_token raises → not authenticated


def test_status_manual_mode_label():
    store_manual_token("x")
    st = get_auth_status()
    assert st["mode"] == "manual"
    assert st["expires_in_days"] is None


def test_corrupt_file_is_not_authenticated():
    session_state.TOKEN_PATH.write_text("{not json!!")
    assert load_token() is None
    assert validate_session() is False


def test_empty_payload_file():
    session_state.TOKEN_PATH.write_text("{}")
    st = get_auth_status()
    assert st["authenticated"] is False


def test_save_failure_wraps_as_session_error(monkeypatch, tmp_path):
    class BoomDir:
        def mkdir(self, *a, **kw):
            raise PermissionError(13, "read-only fs")

    monkeypatch.setattr(session_state, "TOKEN_DIR", BoomDir())
    with pytest.raises(SessionStateError, match="Cannot write"):
        save_token({"access_token": "t"})


def test_saved_file_has_owner_only_permissions():
    import stat

    path = store_manual_token("secret")
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600


def test_expiry_boundary_exactly_now_is_expired(monkeypatch):
    save_token({"access_token": "t", "expires_at": time.time()})
    from swiggy_lyr.exceptions import TokenExpiredError

    with pytest.raises(TokenExpiredError):
        session_state.get_bearer_token()


def test_non_numeric_expiry_ignored():
    save_token({"access_token": "t", "expires_at": "soon"})
    token, source = session_state.get_bearer_token()
    assert (token, source) == ("t", "file")
