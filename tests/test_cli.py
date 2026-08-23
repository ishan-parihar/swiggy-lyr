import pytest

from swiggy_lyr.cli_main import main


def test_status_unauthenticated(capsys, monkeypatch, tmp_path):
    monkeypatch.delenv("SWIGGY_LYR_TOKEN", raising=False)
    monkeypatch.setattr("swiggy_lyr.session_state.TOKEN_PATH", tmp_path / "none.json")
    monkeypatch.setattr("swiggy_lyr.session_state.TOKEN_DIR", tmp_path)
    main(["--status"])
    out = capsys.readouterr().out
    assert "authenticated: false" in out
    assert "help:" in out


def test_status_authenticated_manual(capsys, monkeypatch, tmp_path):
    monkeypatch.delenv("SWIGGY_LYR_TOKEN", raising=False)
    token_file = tmp_path / "token.json"
    token_file.write_text('{"access_token": "abc", "mode": "manual"}')
    monkeypatch.setattr("swiggy_lyr.session_state.TOKEN_PATH", token_file)
    monkeypatch.setattr("swiggy_lyr.session_state.TOKEN_DIR", tmp_path)
    main(["--status"])
    out = capsys.readouterr().out
    assert "authenticated: true" in out
    assert 'mode: "manual"' in out
    assert "expires_in_days" not in out  # manual tokens have no known expiry


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0


def test_login_with_token_flag(capsys, monkeypatch, tmp_path):
    monkeypatch.setattr("swiggy_lyr.session_state.TOKEN_PATH", tmp_path / "t.json")
    monkeypatch.setattr("swiggy_lyr.session_state.TOKEN_DIR", tmp_path)
    main(["--login", "--token", "bearer-abc"])
    out = capsys.readouterr().out
    assert "saved:" in out
    assert 'mode: "manual"' in out
