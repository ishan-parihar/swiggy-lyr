"""Silent refresh + path-scoped discovery against scripted metadata."""

import time

import pytest

from swiggy_lyr import session_state
from swiggy_lyr.exceptions import TokenExpiredError
from swiggy_lyr.oauth import discover_authorization_server, ensure_fresh_token
from tests.test_oauth import FakeAsyncClient, FakeResponse, _patch_httpx


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.delenv("SWIGGY_LYR_TOKEN", raising=False)
    monkeypatch.setattr(session_state, "TOKEN_PATH", tmp_path / "token.json")
    monkeypatch.setattr(session_state, "TOKEN_DIR", tmp_path)


LIVE_META = {
    "issuer": "https://mcp.swiggy.com/auth",
    "authorization_endpoint": "https://mcp.swiggy.com/auth/authorize",
    "token_endpoint": "https://mcp.swiggy.com/auth/token",
    "registration_endpoint": "https://mcp.swiggy.com/auth/register",
    "scopes_supported": ["mcp:tools"],
}


# ── discovery order matches live layout ─────────────────────────────────


async def test_discovery_prefers_path_scoped_metadata(monkeypatch):
    _patch_httpx(monkeypatch)
    seen = []

    real_get = FakeAsyncClient.get

    async def spy_get(self, url):
        seen.append(url)
        return await real_get(self, url)

    monkeypatch.setattr(FakeAsyncClient, "get", spy_get)
    FakeAsyncClient.script = {
        "https://mcp.swiggy.com/.well-known/oauth-protected-resource/food": FakeResponse(
            200,
            {"resource": "r", "authorization_servers": ["https://mcp.swiggy.com/auth"]},
        ),
        "https://mcp.swiggy.com/.well-known/oauth-authorization-server": FakeResponse(
            200, LIVE_META
        ),
    }
    meta = await discover_authorization_server(path="/food")
    assert meta["issuer"].endswith("/auth")
    assert seen[0].endswith("/food")  # path-scoped tried first


async def test_discovery_root_auth_server_short_circuit(monkeypatch):
    """Live Swiggy returns 200 at root oauth-authorization-server — must work."""
    _patch_httpx(monkeypatch)
    FakeAsyncClient.script = {
        # protected-resource root 404s (HTML page) like production
        "https://mcp.swiggy.com/.well-known/oauth-protected-resource": FakeResponse(404),
        "https://mcp.swiggy.com/.well-known/oauth-authorization-server": FakeResponse(
            200, LIVE_META
        ),
    }
    meta = await discover_authorization_server()
    assert meta["token_endpoint"] == "https://mcp.swiggy.com/auth/token"


async def test_discovery_uses_auth_server_path_from_resource(monkeypatch):
    _patch_httpx(monkeypatch)
    FakeAsyncClient.script = {
        "https://mcp.swiggy.com/.well-known/oauth-protected-resource/food": FakeResponse(
            200,
            {"authorization_servers": ["https://mcp.swiggy.com/auth"]},
        ),
        "https://mcp.swiggy.com/.well-known/oauth-authorization-server": FakeResponse(
            200, LIVE_META
        ),
    }
    meta = await discover_authorization_server(path="/food")
    assert meta["registration_endpoint"].endswith("/register")


# ── silent refresh ────────────────────────────────────────────────────────


def _store_oauth(refresh=True, expires_in=None):
    session_state.save_token(
        {
            "access_token": "old",
            "client_id": "swiggy-mcp",
            "expires_at": (time.time() - 10 if expires_in is None else time.time() + expires_in),
            **({"refresh_token": "rt-1"} if refresh else {}),
        }
    )


async def test_expired_with_refresh_token_renews(monkeypatch):
    _store_oauth(refresh=True)  # already expired
    _patch_httpx(monkeypatch)
    FakeAsyncClient.script = {
        "https://mcp.swiggy.com/.well-known/oauth-authorization-server": FakeResponse(
            200, LIVE_META
        ),
        "POST https://mcp.swiggy.com/auth/token": FakeResponse(
            200, {"access_token": "new-tok", "expires_in": 432000, "refresh_token": "rt-2"}
        ),
    }
    token = await ensure_fresh_token()
    assert token == "new-tok"
    stored = session_state.load_token()
    assert stored is not None
    assert stored["access_token"] == "new-tok"
    assert stored["refresh_token"] == "rt-2"  # rotation persisted
    assert stored["mode"] == "oauth"


async def test_valid_token_skips_refresh():
    _store_oauth(refresh=True, expires_in=3600)
    assert await ensure_fresh_token() == "old"


async def test_expired_without_refresh_raises_for_login():
    session_state.save_token({"access_token": "old", "expires_at": time.time() - 5})
    with pytest.raises(TokenExpiredError):
        await ensure_fresh_token()


async def test_refresh_grant_rejected_raises_original(monkeypatch):
    _store_oauth(refresh=True)
    _patch_httpx(monkeypatch)
    FakeAsyncClient.script = {
        "https://mcp.swiggy.com/.well-known/oauth-authorization-server": FakeResponse(
            200, LIVE_META
        ),
        "POST https://mcp.swiggy.com/auth/token": FakeResponse(400, {"error": "invalid_grant"}),
    }
    with pytest.raises(TokenExpiredError):
        await ensure_fresh_token()


async def test_refresh_network_error_raises_original(monkeypatch):
    import httpx

    _store_oauth(refresh=True)
    _patch_httpx(monkeypatch)
    err = httpx.ConnectError("down")
    FakeAsyncClient.script = {
        "https://mcp.swiggy.com/.well-known/oauth-authorization-server": err,
    }
    with pytest.raises(TokenExpiredError):
        await ensure_fresh_token()


async def test_manual_mode_never_refreshes():
    session_state.store_manual_token("x")
    session_state.TOKEN_PATH.write_text(
        '{"access_token":"x","mode":"manual","expires_at":1}'
    )  # expired but manual → no refresh_token key
    with pytest.raises(TokenExpiredError):
        await ensure_fresh_token()


def _FakeResponse(status_code, payload):  # alias for brevity in this module
    from tests.test_oauth import FakeResponse as R

    return R(status_code, payload)
