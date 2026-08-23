"""Edge-case tests for oauth.py using a fake httpx.AsyncClient (no network)."""

import pytest

from swiggy_lyr import oauth
from swiggy_lyr.exceptions import OAuthError
from swiggy_lyr.oauth import (
    discover_authorization_server,
    exchange_code,
    make_pkce_pair,
    register_client,
    validate_callback_query,
)


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text or str(self._payload)

    def json(self):
        return self._payload


class FakeAsyncClient:
    """Scripted httpx.AsyncClient stand-in.

    script maps URL → FakeResponse, an Exception instance (raised verbatim,
    e.g. httpx.ConnectError), or absent = HTTP 404.
    """

    script: dict[str, object] = {}
    posts: list = []

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url):
        resp = self.script.get(url, FakeResponse(404))
        if isinstance(resp, Exception):
            raise resp
        return resp

    async def post(self, url, **kw):
        self.posts.append((url, kw))
        resp = self.script.get(f"POST {url}", FakeResponse(404))
        if isinstance(resp, Exception):
            raise resp
        return resp


@pytest.fixture(autouse=True)
def _reset():
    FakeAsyncClient.script = {}
    FakeAsyncClient.posts = []


def _patch_httpx(monkeypatch):
    monkeypatch.setattr(oauth.httpx, "AsyncClient", FakeAsyncClient)


AUTH_META = {
    "authorization_endpoint": "https://auth.swiggy.com/authorize",
    "token_endpoint": "https://auth.swiggy.com/token",
    "registration_endpoint": "https://auth.swiggy.com/register",
    "scopes_supported": ["mcp:tools"],
}


# ── discovery ────────────────────────────────────────────────────────────


async def test_discovery_via_protected_resource(monkeypatch):
    _patch_httpx(monkeypatch)
    FakeAsyncClient.script = {
        "https://mcp.swiggy.com/.well-known/oauth-protected-resource": FakeResponse(
            200, {"authorization_servers": ["https://auth.swiggy.com"]}
        ),
        "https://auth.swiggy.com/.well-known/oauth-authorization-server": FakeResponse(
            200, AUTH_META
        ),
    }
    meta = await discover_authorization_server()
    assert meta["token_endpoint"].endswith("/token")


async def test_discovery_direct_auth_server_fallback(monkeypatch):
    _patch_httpx(monkeypatch)
    FakeAsyncClient.script = {
        "https://mcp.swiggy.com/.well-known/oauth-authorization-server": FakeResponse(
            200, AUTH_META
        ),
    }
    meta = await discover_authorization_server()
    assert meta["authorization_endpoint"]


async def test_discovery_network_error_raises(monkeypatch):
    import httpx

    _patch_httpx(monkeypatch)
    err = httpx.ConnectError("down")
    FakeAsyncClient.script = {
        "https://mcp.swiggy.com/.well-known/oauth-protected-resource": err,
        "https://mcp.swiggy.com/.well-known/oauth-authorization-server": err,
    }
    with pytest.raises(OAuthError, match="Cannot reach"):
        await discover_authorization_server()


async def test_discovery_missing_metadata_raises(monkeypatch):
    _patch_httpx(monkeypatch)
    FakeAsyncClient.script = {
        "https://mcp.swiggy.com/.well-known/oauth-protected-resource": FakeResponse(
            200, {"authorization_servers": ["https://auth.swiggy.com"]}
        )
        # auth-server metadata route absent → 404 inside second hop
    }
    with pytest.raises(OAuthError):
        await discover_authorization_server()


# ── dynamic client registration ──────────────────────────────────────────


async def test_dcr_success(monkeypatch):
    _patch_httpx(monkeypatch)
    FakeAsyncClient.script["POST https://auth.swiggy.com/register"] = FakeResponse(
        201, {"client_id": "cid-123"}
    )
    assert await register_client(AUTH_META, "http://localhost:9876/callback") == "cid-123"


async def test_dcr_failure_no_env_raises(monkeypatch):
    _patch_httpx(monkeypatch)
    monkeypatch.delenv("SWIGGY_LYR_CLIENT_ID", raising=False)
    with pytest.raises(OAuthError, match="Dynamic client registration failed"):
        await register_client(AUTH_META, "http://x/callback")


async def test_dcr_failure_env_fallback(monkeypatch):
    _patch_httpx(monkeypatch)
    monkeypatch.setenv("SWIGGY_LYR_CLIENT_ID", "pre-registered")
    assert await register_client(AUTH_META, "http://x/callback") == "pre-registered"


async def test_no_registration_endpoint_with_env(monkeypatch):
    monkeypatch.setenv("SWIGGY_LYR_CLIENT_ID", "cid-env")
    assert await register_client({}, "http://x/callback") == "cid-env"


async def test_no_registration_endpoint_no_env_raises(monkeypatch):
    monkeypatch.delenv("SWIGGY_LYR_CLIENT_ID", raising=False)
    with pytest.raises(OAuthError, match="no registration endpoint"):
        await register_client({}, "http://x/callback")


# ── token exchange ───────────────────────────────────────────────────────


async def test_exchange_success(monkeypatch):
    _patch_httpx(monkeypatch)
    FakeAsyncClient.script["POST https://auth.swiggy.com/token"] = FakeResponse(
        200, {"access_token": "tok", "expires_in": 432000}
    )
    out = await exchange_code(AUTH_META, "code1", "verifier", "cid", "http://x/cb")
    assert out["access_token"] == "tok"


async def test_exchange_failure_raises(monkeypatch):
    _patch_httpx(monkeypatch)
    FakeAsyncClient.script["POST https://auth.swiggy.com/token"] = FakeResponse(
        400, {"error": "invalid_grant"}
    )
    with pytest.raises(OAuthError, match="Token exchange failed"):
        await exchange_code(AUTH_META, "bad", "v", "cid", "http://x/cb")


async def test_exchange_missing_endpoint_raises(monkeypatch):
    with pytest.raises(OAuthError, match="token_endpoint"):
        await exchange_code({}, "c", "v", "cid", "http://x/cb")


# ── callback query validation (CSRF / denial / missing code) ────────────


def test_callback_happy_path():
    code = validate_callback_query({"code": ["c1"], "state": ["s1"]}, "s1")
    assert code == "c1"


def test_callback_provider_error():
    with pytest.raises(OAuthError, match="Authorization denied"):
        validate_callback_query(
            {"error": ["access_denied"], "error_description": ["user said no"], "state": ["s"]}, "s"
        )


def test_callback_missing_code():
    with pytest.raises(OAuthError, match="no authorization code"):
        validate_callback_query({"state": ["s"]}, "s")


def test_callback_state_mismatch_csrf():
    with pytest.raises(OAuthError, match="state mismatch"):
        validate_callback_query({"code": ["c"], "state": ["evil"]}, "expected")
    # state missing entirely also mismatches
    with pytest.raises(OAuthError, match="state mismatch"):
        validate_callback_query({"code": ["c"]}, "expected")


# ── PKCE ─────────────────────────────────────────────────────────────────


def test_pkce_s256_math():
    import base64
    import hashlib

    verifier, challenge = make_pkce_pair()
    digest = hashlib.sha256(verifier.encode()).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    assert challenge == expected
