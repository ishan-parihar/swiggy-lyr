"""Final edge sweep: full OAuth flow, loop-context discovery, empty-server guard."""

import pytest
from fastmcp import FastMCP

from swiggy_lyr import oauth
from swiggy_lyr.exceptions import NotAuthenticatedError, OAuthError, UpstreamError
from swiggy_lyr.oauth import run_login_flow
from swiggy_lyr.server import create_mcp_server
from swiggy_lyr.upstream.proxy import register_stream_tools
from tests.fakes import FAKE_TOOLS

# ── unauthenticated upstream calls keep their typed error ───────────────


async def test_call_without_auth_raises_not_authenticated(monkeypatch, tmp_path):
    from swiggy_lyr.upstream.client import call_stream_tool

    monkeypatch.delenv("SWIGGY_LYR_TOKEN", raising=False)
    monkeypatch.setattr("swiggy_lyr.session_state.TOKEN_PATH", tmp_path / "none.json")
    monkeypatch.setattr("swiggy_lyr.session_state.TOKEN_DIR", tmp_path)
    with pytest.raises(NotAuthenticatedError):
        await call_stream_tool("https://x", "any_tool", {})


async def test_list_without_auth_raises_not_authenticated(monkeypatch, tmp_path):
    from swiggy_lyr.upstream.client import list_stream_tools

    monkeypatch.delenv("SWIGGY_LYR_TOKEN", raising=False)
    monkeypatch.setattr("swiggy_lyr.session_state.TOKEN_PATH", tmp_path / "none.json")
    monkeypatch.setattr("swiggy_lyr.session_state.TOKEN_DIR", tmp_path)
    with pytest.raises(NotAuthenticatedError):
        await list_stream_tools("https://x")


# ── discovery works inside a running event loop (thread bridge) ─────────


async def test_discovery_inside_running_loop():
    async def good_lister(url):
        return FAKE_TOOLS

    mcp = FastMCP("inloop")
    count = register_stream_tools(mcp, "food", "https://fake", tools=None, lister=good_lister)
    assert count == len(FAKE_TOOLS)


async def test_discovery_failure_inside_running_loop_returns_zero():
    async def bad_lister(url):
        raise UpstreamError("down")

    mcp = FastMCP("inloop2")
    assert register_stream_tools(mcp, "food", "https://fake", tools=None, lister=bad_lister) == 0


# ── server refuses to start an empty tool surface ────────────────────────


def test_server_with_all_streams_down_raises(monkeypatch):
    def dead(mcp):
        return 0

    monkeypatch.setattr("swiggy_lyr.server.register_food_tools", dead)
    monkeypatch.setattr("swiggy_lyr.server.register_instamart_tools", dead)
    monkeypatch.setattr("swiggy_lyr.server.register_dineout_tools", dead)
    with pytest.raises(UpstreamError, match="0 tools"):
        create_mcp_server()


def test_server_partial_stream_loss_still_serves(monkeypatch):
    from tests.fakes import fake_caller

    def only_food(mcp):
        return register_stream_tools(
            mcp, "food", "https://fake", tools=FAKE_TOOLS[:1], caller=fake_caller
        )

    def dead(mcp):
        return 0

    # patch names in server's namespace (server.py imported them directly)
    monkeypatch.setattr("swiggy_lyr.server.register_food_tools", only_food)
    monkeypatch.setattr("swiggy_lyr.server.register_instamart_tools", dead)
    monkeypatch.setattr("swiggy_lyr.server.register_dineout_tools", dead)
    mcp = create_mcp_server()  # must not raise
    names = {t.name for t in _run(mcp.list_tools())}
    assert names == {"food_search_restaurants"}


def _run(coro):
    import asyncio

    try:
        asyncio.get_running_loop()
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(1) as p:
            return p.submit(asyncio.run, coro).result()
    except RuntimeError:
        return asyncio.run(coro)


# ── full login flow with every network touch faked ───────────────────────


async def test_login_flow_happy_path(monkeypatch, capsys, tmp_path):
    from swiggy_lyr import session_state

    monkeypatch.setattr(session_state, "TOKEN_PATH", tmp_path / "token.json")
    monkeypatch.setattr(session_state, "TOKEN_DIR", tmp_path)

    meta = {
        "authorization_endpoint": "https://auth/az",
        "token_endpoint": "https://auth/tok",
        "registration_endpoint": "https://auth/reg",
        "scopes_supported": ["mcp:tools"],
    }

    async def fake_discover(origin=None):
        return meta

    async def fake_register(m, redirect_uri):
        captured["redirect_uri"] = redirect_uri
        return "cid-1"

    async def fake_exchange(m, code, verifier, client_id, redirect_uri):
        assert code == "auth-code"
        assert client_id == "cid-1"
        return {"access_token": "tok-1", "expires_in": 432000}

    captured = {}
    monkeypatch.setattr(oauth, "discover_authorization_server", fake_discover)
    monkeypatch.setattr(oauth, "register_client", fake_register)
    monkeypatch.setattr(oauth, "exchange_code", fake_exchange)
    # pin the CSRF state so the scripted callback can echo it
    monkeypatch.setattr(oauth.secrets, "token_urlsafe", lambda n: "s")
    monkeypatch.setattr(
        oauth,
        "wait_for_callback",
        lambda port, timeout=300: {"code": ["auth-code"], "state": ["s"]},
    )
    monkeypatch.setattr("webbrowser.open", lambda url: True)

    payload = await run_login_flow()
    out = capsys.readouterr().out

    assert payload["access_token"] == "tok-1"
    assert payload["mode"] == "oauth"
    assert captured["redirect_uri"] == "http://localhost:9876/callback"
    assert "saved" in out.lower()

    stored = session_state.load_token()
    assert stored is not None and stored["access_token"] == "tok-1"

    # and the token actually authenticates subsequent calls
    token, source = session_state.get_bearer_token()
    assert (token, source) == ("tok-1", "file")


async def test_login_flow_state_mismatch_aborts(monkeypatch, tmp_path):
    from swiggy_lyr import session_state

    monkeypatch.setattr(session_state, "TOKEN_PATH", tmp_path / "t.json")
    monkeypatch.setattr(session_state, "TOKEN_DIR", tmp_path)

    async def fake_discover(origin=None):
        return {
            "authorization_endpoint": "https://auth/az",
            "token_endpoint": "https://auth/tok",
            "registration_endpoint": "https://auth/reg",
        }

    async def fake_register(m, r):
        return "cid"

    async def fake_exchange(*a):
        raise AssertionError("must not reach exchange on state mismatch")

    monkeypatch.setattr(oauth, "discover_authorization_server", fake_discover)
    monkeypatch.setattr(oauth, "register_client", fake_register)
    monkeypatch.setattr(oauth, "exchange_code", fake_exchange)
    monkeypatch.setattr(
        oauth, "wait_for_callback", lambda port, timeout=300: {"code": ["c"], "state": ["evil"]}
    )
    monkeypatch.setattr("webbrowser.open", lambda url: True)

    with pytest.raises(OAuthError, match="state mismatch"):
        await run_login_flow()


async def test_login_flow_provider_denial(monkeypatch, tmp_path):
    from swiggy_lyr import session_state

    monkeypatch.setattr(session_state, "TOKEN_PATH", tmp_path / "t.json")
    monkeypatch.setattr(session_state, "TOKEN_DIR", tmp_path)

    async def fake_discover(origin=None):
        return {"authorization_endpoint": "a", "token_endpoint": "t", "registration_endpoint": "r"}

    async def fake_register(m, r):
        return "cid"

    monkeypatch.setattr(oauth, "discover_authorization_server", fake_discover)
    monkeypatch.setattr(oauth, "register_client", fake_register)
    monkeypatch.setattr(
        oauth,
        "wait_for_callback",
        lambda port, timeout=300: {"error": ["access_denied"], "state": ["s"]},
    )
    monkeypatch.setattr("webbrowser.open", lambda url: True)

    with pytest.raises(OAuthError, match="access_denied"):
        await run_login_flow()


# ── callback timeout on a real socket ────────────────────────────────────


def test_callback_timeout_raises_quickly(tmp_path):
    import time as _time

    oauth._CallbackState.done.clear()
    start = _time.monotonic()
    with pytest.raises(OAuthError, match="Timed out"):
        oauth.wait_for_callback(port=0 or _free_port(), timeout=1)
    assert _time.monotonic() - start < 5


def _free_port() -> int:
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port
