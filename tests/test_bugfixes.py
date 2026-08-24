"""Regression suite for the live-verified bug sweep (B1–B16).

Each test pins a failure mode observed against production Swiggy or through
adversarial local probing — flat-fake-only tests let these slip through once;
never again.
"""

import asyncio
import json
import socket
import threading
import time
import urllib.error
import urllib.request

import httpx
import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from swiggy_lyr import oauth, session_state
from swiggy_lyr.cli_main import main
from swiggy_lyr.exceptions import OAuthError, TokenExpiredError, UpstreamError
from swiggy_lyr.upstream.client import _normalize, _translate
from swiggy_lyr.upstream.proxy import (
    discover_streams,
    make_tool,
    register_stream_tools,
)
from tests.fakes import FAKE_TOOLS, fake_caller

# ── helpers ───────────────────────────────────────────────────────────────


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _http_401() -> Exception:
    """The exact shape the mcp SDK buries inside its TaskGroup."""
    req = httpx.Request("POST", "https://mcp.swiggy.com/food")
    resp = httpx.Response(401, request=req, text='{"error": "invalid_token"}')
    return httpx.HTTPStatusError("Client error '401 Unauthorized'", request=req, response=resp)


def _build(schema_by_name: dict, caller=fake_caller, extra_tools=None):
    mcp = FastMCP("bugfix")
    tools = [{"name": n, "description": "d", "inputSchema": s} for n, s in schema_by_name.items()]
    tools += extra_tools or []
    count = register_stream_tools(mcp, "food", "https://fake", tools=tools, caller=caller)
    return mcp, count


# ── B1: 401 detection inside ExceptionGroups ─────────────────────────────


def test_b1_group_wrapped_401_is_token_expired():
    err = _translate(ExceptionGroup("unhandled errors in a TaskGroup", [_http_401()]), "u")
    assert isinstance(err, TokenExpiredError)
    assert "--login" in err.hint


def test_b1_nested_groups_still_detected():
    inner = ExceptionGroup("inner", [ValueError("noise"), _http_401()])
    err = _translate(ExceptionGroup("outer", [inner]), "u")
    assert isinstance(err, TokenExpiredError)


def test_b1_group_with_transport_error_is_upstream():
    err = _translate(ExceptionGroup("tg", [httpx.ConnectError("connection refused")]), "https://x")
    assert isinstance(err, UpstreamError)
    assert "Cannot reach" in err.message


def test_b1_group_generic_keeps_deepest_cause():
    err = _translate(ExceptionGroup("tg", [RuntimeError("kaboom")]), "https://x")
    assert isinstance(err, UpstreamError)
    assert "kaboom" in str(err)


def test_b1_plain_exceptions_still_translated():
    assert isinstance(_translate(Exception("HTTP 401"), "u"), TokenExpiredError)
    assert isinstance(_translate(ConnectionError("refused"), "u"), UpstreamError)


# ── B6: isError survives structured results ──────────────────────────────


def test_b6_normalize_flags_structured_errors():
    class R:
        structuredContent = {"cart": None}
        content = []
        isError = True

    out = _normalize(R())
    assert out == {"data": {"cart": None}, "is_error": True}


# ── B2: hostile upstream parameter names ─────────────────────────────────


async def test_b2_hyphenated_param_round_trips_original_name():
    mcp, n = _build(
        {
            "weird": {
                "type": "object",
                "properties": {"restaurant-id": {"type": "string"}},
                "required": ["restaurant-id"],
            }
        }
    )
    assert n == 1
    async with Client(mcp) as c:
        result = await c.call_tool("food_weird", {"restaurant_id": "r1"})
    assert result.is_error is False
    from tests.fakes import CALLS

    assert CALLS[-1][2] == {"restaurant-id": "r1"}


async def test_b2_reserved_word_and_collision_params():
    mcp, n = _build(
        {
            "kw": {
                "type": "object",
                "properties": {"class": {"type": "string"}},
                "required": ["class"],
            },
            "collide": {
                "type": "object",
                "properties": {
                    "full-name": {"type": "string"},
                    "full_name": {"type": "string"},
                },
                "required": ["full-name"],
            },
        }
    )
    assert n == 2  # registration survived both
    async with Client(mcp) as c:
        r1 = await c.call_tool("food_kw", {"class_": "v1"})
        r2 = await c.call_tool("food_collide", {"full_name": "a", "full_name_2": "b"})
    assert r1.is_error is False and r2.is_error is False
    from tests.fakes import CALLS

    assert CALLS[-2][2] == {"class": "v1"}
    assert CALLS[-1][2] == {"full-name": "a", "full_name": "b"}


async def test_b2_bad_tool_skipped_siblings_live(monkeypatch):
    import swiggy_lyr.upstream.proxy as proxy

    real_make_tool = proxy.make_tool

    def picky(name, desc, schema, url, caller):
        if name == "cursed":
            raise ValueError("'cursed' cannot be wrapped")
        return real_make_tool(name, desc, schema, url, caller)

    monkeypatch.setattr(proxy, "make_tool", picky)
    mcp, n = _build(
        {},
        extra_tools=[
            {"name": "cursed", "description": "", "inputSchema": {"type": "object"}},
            {"name": "healthy", "description": "", "inputSchema": {"type": "object"}},
        ],
    )
    assert n == 1
    names = {t.name for t in await mcp.list_tools()}
    assert names == {"food_healthy"}


# ── B3: upstream-native confirm param ────────────────────────────────────


async def test_b3_native_confirm_registers_and_gates(monkeypatch):
    from tests.fakes import CALLS

    schema = {
        "type": "object",
        "properties": {"confirm": {"type": "boolean", "description": "final go"}},
        "required": [],
    }
    mcp, n = _build({"checkout": schema})
    assert n == 1  # duplicate-param crash is gone

    monkeypatch.delenv("SWIGGY_LYR_ALLOW_ORDERS", raising=False)
    async with Client(mcp) as c:
        with pytest.raises(ToolError, match="ALLOW_ORDERS"):
            await c.call_tool("food_checkout", {"confirm": True})

        monkeypatch.setenv("SWIGGY_LYR_ALLOW_ORDERS", "1")
        with pytest.raises(ToolError, match="confirm"):
            await c.call_tool("food_checkout", {})  # omitted → falsy → blocked

        ok = await c.call_tool("food_checkout", {"confirm": True})
        assert not ok.is_error
        assert CALLS[-1][2] == {"confirm": True}  # native value forwarded upstream


# ── B4: null-leak elimination + schema defaults ──────────────────────────


async def test_b4_omitted_optionals_never_leak_nulls():
    from tests.fakes import CALLS

    schema = {
        "type": "object",
        "properties": {
            "q": {"type": "string"},
            "limit": {"type": "integer", "default": 10},
            "tag": {"type": "string"},
        },
        "required": ["q"],
    }
    fn = make_tool("search", "", schema, "https://x", fake_caller)
    out = await fn(q="dosa")
    assert out["data"]["echo_args"] == {"q": "dosa"}

    mcp, _ = _build({"search": schema})
    async with Client(mcp) as c:
        await c.call_tool("food_search", {"q": "dosa"})  # omit both optionals
        assert CALLS[-1][2] == {"q": "dosa", "limit": 10}  # schema default applied

        await c.call_tool("food_search", {"q": "dosa", "tag": None})  # explicit null dropped
        assert CALLS[-1][2] == {"q": "dosa", "limit": 10}

        await c.call_tool("food_search", {"q": "dosa", "limit": 5})  # override wins
        assert CALLS[-1][2] == {"q": "dosa", "limit": 5}


# ── B5: descriptions/enums/defaults reach clients ────────────────────────


async def test_b5_rich_metadata_in_exposed_schema():
    schema = {
        "type": "object",
        "properties": {
            "q": {"type": "string", "description": "dish name"},
            "sort": {
                "type": "string",
                "enum": ["rating", "cost"],
                "description": "sort order",
                "default": "rating",
            },
        },
        "required": ["q"],
    }
    mcp, n = _build({"search": schema})
    assert n == 1
    async with Client(mcp) as c:
        (tool,) = await c.list_tools()
    props = tool.inputSchema["properties"]
    assert props["q"]["description"] == "dish name"
    assert props["sort"]["description"] == "sort order"
    assert "rating" in json.dumps(props["sort"])  # enum survived
    assert props["sort"]["default"] == "rating"


# ── B8: parallel discovery with per-stream isolation ─────────────────────


async def test_b8_discover_streams_parallel_and_isolated(caplog):
    async def lister(url):
        await asyncio.sleep(0.2)
        if "bad" in url:
            raise UpstreamError("down")
        return [{"name": url}]

    streams = {"alpha": "u-alpha", "beta": "u-bad", "gamma": "u-gamma"}
    t0 = time.monotonic()
    out = await discover_streams(streams, lister=lister)
    elapsed = time.monotonic() - t0

    assert elapsed < 0.55  # serial would cost >= 0.60
    assert out["beta"] == []  # failure isolated
    assert [out[k][0]["name"] for k in ("alpha", "gamma")] == ["u-alpha", "u-gamma"]
    assert any("beta" in rec.getMessage() for rec in caplog.records)


# ── B7/B18: login flow ordering + callback path filter ───────────────────


@pytest.fixture()
def _isolated_token_store(monkeypatch, tmp_path):
    monkeypatch.delenv("SWIGGY_LYR_TOKEN", raising=False)
    monkeypatch.setattr(session_state, "TOKEN_PATH", tmp_path / "token.json")
    monkeypatch.setattr(session_state, "TOKEN_DIR", tmp_path)


def _oauth_fakes(monkeypatch, client_id="cid-1", token="tok-1"):
    meta = {
        "authorization_endpoint": "https://auth/az",
        "token_endpoint": "https://auth/tok",
        "registration_endpoint": "https://auth/reg",
        "scopes_supported": ["mcp:tools"],
    }

    async def fake_discover(origin=None):
        return meta

    async def fake_register(m, redirect_uri):
        return client_id

    async def fake_exchange(m, code, verifier, cid, redirect_uri):
        assert code == "auth-code"
        return {"access_token": token, "expires_in": 432000}

    monkeypatch.setattr(oauth, "discover_authorization_server", fake_discover)
    monkeypatch.setattr(oauth, "register_client", fake_register)
    monkeypatch.setattr(oauth, "exchange_code", fake_exchange)
    monkeypatch.setattr(oauth.secrets, "token_urlsafe", lambda n: "s")


def _wait_listening(port: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        s = socket.socket()
        s.settimeout(0.2)
        try:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        finally:
            s.close()
        time.sleep(0.05)
    raise AssertionError(f"callback server never bound {port}")


async def test_b7_server_bound_before_browser_opens(monkeypatch, _isolated_token_store):
    port = _free_port()
    monkeypatch.setenv("SWIGGY_LYR_PORT", str(port))
    _oauth_fakes(monkeypatch)

    seen: dict = {}

    def fake_open(url):
        s = socket.socket()
        s.settimeout(1.0)
        try:
            seen["bound_at_open"] = s.connect_ex(("127.0.0.1", port)) == 0
        finally:
            s.close()
        return True

    monkeypatch.setattr(oauth.webbrowser, "open", fake_open)

    def traffic():
        _wait_listening(port)
        # B18: noise WITH query params on a non-callback path must be ignored
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/favicon.ico?code=x&state=y", timeout=5)
        except urllib.error.HTTPError as e:
            assert e.code == 404
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/callback?code=auth-code&state=s", timeout=5
        ) as r:
            assert r.status == 200

    t = threading.Thread(target=traffic, daemon=True)
    t.start()
    payload = await oauth.run_login_flow()

    t.join(timeout=5)
    assert not t.is_alive()
    assert seen["bound_at_open"] is True  # regression: server used to bind AFTER the browser opened
    assert payload["access_token"] == "tok-1"
    stored = session_state.load_token()
    assert stored is not None and stored["client_id"] == "cid-1"


async def test_b7_port_busy_is_typed_error(monkeypatch, _isolated_token_store):
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    port = blocker.getsockname()[1]
    monkeypatch.setenv("SWIGGY_LYR_PORT", str(port))
    _oauth_fakes(monkeypatch)

    opened = []
    monkeypatch.setattr(oauth.webbrowser, "open", lambda url: opened.append(url))

    with pytest.raises(OAuthError, match="Cannot bind callback port") as ei:
        await oauth.run_login_flow()
    assert "SWIGGY_LYR_PORT" in ei.value.hint
    assert opened == []  # browser never opened on a dead port


# ── B15: refresh refuses without stored client_id ────────────────────────


async def test_b15_refresh_requires_stored_client_id(monkeypatch, _isolated_token_store):
    session_state.save_token(
        {
            "access_token": "old",
            "refresh_token": "rt-1",
            "expires_at": time.time() - 10,
            # no client_id — guessing one must be refused
        }
    )
    assert await oauth.try_refresh_stored_token() is None


# ── B10/B11: CLI traps ───────────────────────────────────────────────────


def test_b10_token_without_login_rejected(capsys):
    with pytest.raises(SystemExit) as ei:
        main(["--token", "bearer-abc"])
    assert ei.value.code == 2
    assert "--login --token" in capsys.readouterr().out


def test_b11_generic_exception_rendered_as_toon(capsys, monkeypatch):
    def boom():
        raise ValueError("disk on fire")

    monkeypatch.setattr("swiggy_lyr.cli_main.cmd_status", boom)
    with pytest.raises(SystemExit) as ei:
        main(["--status"])
    assert ei.value.code == 2
    out = capsys.readouterr().out
    assert 'error: "ValueError: disk on fire"' in out


# ── assembly sanity: DI server build end-to-end over MCP protocol ────────


async def test_server_di_build_serves_prefixed_tools(monkeypatch):
    from swiggy_lyr.server import create_mcp_server
    from swiggy_lyr.upstream import client as client_mod

    async def fake_call(url, name, args):
        return {"data": {"tool": name}}

    monkeypatch.setattr(client_mod, "call_stream_tool", fake_call)
    mcp = create_mcp_server(discovered={"food": FAKE_TOOLS[:2], "instamart": [], "dineout": []})
    async with Client(mcp) as c:
        names = {t.name for t in await c.list_tools()}
        assert names == {"food_search_restaurants", "food_get_menu"}
        res = await c.call_tool("food_get_menu", {"restaurant_id": "r1"})
        assert not res.is_error
        assert "get_menu" in res.content[0].text
