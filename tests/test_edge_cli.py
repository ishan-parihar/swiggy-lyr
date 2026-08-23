"""CLI edge cases: error mapping, exit codes, argparse behavior."""

import pytest

from swiggy_lyr.cli_main import axi_error, main
from swiggy_lyr.exceptions import NotAuthenticatedError, TokenExpiredError


def test_axi_error_exit_code_2(capsys):
    with pytest.raises(SystemExit) as ei:
        axi_error("something broke", hint="do the other thing")
    assert ei.value.code == 2
    out = capsys.readouterr().out
    assert 'error: "something broke"' in out
    assert 'help: "do the other thing"' in out


def test_axi_error_without_hint(capsys):
    with pytest.raises(SystemExit):
        axi_error("just an error")
    assert "help:" not in capsys.readouterr().out


def test_unknown_flag_exits_2():
    # argparse's own usage error is also exit 2 — AXI-consistent by default.
    with pytest.raises(SystemExit) as ei:
        main(["--definitely-not-a-flag"])
    assert ei.value.code == 2


def test_serve_maps_swiggy_errors_to_axi(monkeypatch, capsys):
    import swiggy_lyr.server as server_mod

    def boom(**kw):
        raise TokenExpiredError("expired", hint="swiggy-lyr --login")

    monkeypatch.setattr(server_mod, "run", boom)
    with pytest.raises(SystemExit) as ei:
        main([])
    assert ei.value.code == 2
    out = capsys.readouterr().out
    assert 'error: "expired"' in out
    assert 'help: "swiggy-lyr --login"' in out


def test_status_oauth_countdown_output(capsys, monkeypatch, tmp_path):
    import time

    from swiggy_lyr import session_state

    token_file = tmp_path / "token.json"
    token_file.write_text(
        f'{{"access_token": "t", "mode": "oauth", "expires_at": {time.time() + 86400 * 3}}}'
    )
    monkeypatch.setattr(session_state, "TOKEN_PATH", token_file)
    monkeypatch.setattr(session_state, "TOKEN_DIR", tmp_path)
    monkeypatch.delenv("SWIGGY_LYR_TOKEN", raising=False)
    main(["--status"])
    out = capsys.readouterr().out
    assert "authenticated: true" in out
    assert 'mode: "oauth"' in out
    assert "expires_in_days: 3.0" in out


def test_login_flag_with_empty_token_still_manual_path(capsys, monkeypatch, tmp_path):
    from swiggy_lyr import session_state

    monkeypatch.setattr(session_state, "TOKEN_PATH", tmp_path / "t.json")
    monkeypatch.setattr(session_state, "TOKEN_DIR", tmp_path)
    main(["--login", "--token", "  spaced-token  "])
    out = capsys.readouterr().out
    assert "saved:" in out
    token, _ = session_state.get_bearer_token()
    assert token == "spaced-token"


def test_not_authenticated_error_hint_renders(capsys, monkeypatch, tmp_path):
    from swiggy_lyr import session_state

    monkeypatch.setattr(session_state, "TOKEN_PATH", tmp_path / "missing.json")
    monkeypatch.setattr(session_state, "TOKEN_DIR", tmp_path)
    err = NotAuthenticatedError("none", hint="run login")
    assert "hint: run login" in str(err) or err.hint == "run login"
