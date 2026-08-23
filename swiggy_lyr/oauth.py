"""OAuth 2.1 + PKCE against Swiggy's MCP authorization server.

Flow (RFC 9728 discovery → RFC 7591 dynamic client registration → PKCE):
1. Discover protected-resource metadata at mcp.swiggy.com → authorization server.
2. Fetch authorization-server metadata (endpoints, scopes, registration).
3. Register "swiggy-lyr" as a public client via dynamic client registration
   (falls back to SWIGGY_LYR_CLIENT_ID env if DCR is unavailable).
4. Open the browser for user consent; catch the redirect on a local HTTP server.
5. Exchange the code and persist the token (~5 day lifetime).
"""

import base64
import hashlib
import os
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import httpx

from swiggy_lyr.exceptions import OAuthError
from swiggy_lyr.logging_config import logger
from swiggy_lyr.session_state import save_token

MCP_ORIGIN = "https://mcp.swiggy.com"
DEFAULT_SCOPE = "mcp:tools"
DEFAULT_PORT = 9876
CALLBACK_TIMEOUT = 300  # 5 min to complete browser consent


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def make_pkce_pair() -> tuple[str, str]:
    """(code_verifier, code_challenge) using S256."""
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


async def discover_authorization_server(origin: str = MCP_ORIGIN) -> dict:
    """RFC 9728 discovery. Returns authorization-server metadata."""
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        resource_meta = None
        for url in (
            f"{origin}/.well-known/oauth-protected-resource",
            f"{origin}/.well-known/oauth-authorization-server",
        ):
            try:
                resp = await client.get(url)
            except httpx.HTTPError as e:
                raise OAuthError(f"Cannot reach {origin}: {e}") from e
            if resp.status_code == 200:
                doc = resp.json()
                # Root auth-server metadata found directly.
                if url.endswith("oauth-authorization-server"):
                    return doc
                resource_meta = doc
                break

        servers = (resource_meta or {}).get("authorization_servers") or [origin]
        base = str(servers[0]).rstrip("/")
        root = f"{urlparse(base).scheme}://{urlparse(base).netloc}"
        resp = await client.get(f"{root}/.well-known/oauth-authorization-server")
        if resp.status_code != 200:
            raise OAuthError(
                f"No authorization-server metadata at {root}",
                hint="Swiggy may have changed its OAuth layout — check their manifest repo.",
            )
        return resp.json()


async def register_client(meta: dict, redirect_uri: str) -> str | None:
    """Dynamic client registration. Returns client_id or None (use env fallback)."""
    endpoint = meta.get("registration_endpoint")
    env_client = os.environ.get("SWIGGY_LYR_CLIENT_ID")
    if not endpoint:
        if env_client:
            return env_client
        raise OAuthError(
            "Server exposes no registration endpoint and no SWIGGY_LYR_CLIENT_ID set",
            hint="Set SWIGGY_LYR_CLIENT_ID with a pre-registered OAuth client id.",
        )
    payload = {
        "client_name": "swiggy-lyr",
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "code_challenge_method": "S256",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(endpoint, json=payload)
    if resp.status_code in (200, 201):
        return resp.json().get("client_id")
    if env_client:
        logger.warning("DCR failed (%s); falling back to SWIGGY_LYR_CLIENT_ID", resp.status_code)
        return env_client
    raise OAuthError(f"Dynamic client registration failed: {resp.status_code} {resp.text[:200]}")


class _CallbackState:
    query: dict[str, list[str]] | None = None
    done = threading.Event()


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (http.server API)
        _CallbackState.query = parse_qs(urlparse(self.path).query)
        body = b"<h2>swiggy-lyr</h2><p>Authorized. You can close this window.</p>"
        self.send_response(200 if "code" in (_CallbackState.query or {}) else 400)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        _CallbackState.done.set()

    def log_message(self, *args):  # silence per-request stderr spam
        pass


def wait_for_callback(port: int, timeout: int = CALLBACK_TIMEOUT) -> dict[str, list[str]]:
    """Block until the browser hits our loopback callback. Returns query params."""
    server = HTTPServer(("127.0.0.1", port), _CallbackHandler)
    server.timeout = 1
    deadline = time.time() + timeout
    try:
        while not _CallbackState.done.is_set():
            if time.time() > deadline:
                raise OAuthError("Timed out waiting for OAuth redirect")
            server.handle_request()
    finally:
        server.server_close()
    return _CallbackState.query or {}


async def exchange_code(
    meta: dict, code: str, verifier: str, client_id: str, redirect_uri: str
) -> dict:
    token_ep = meta.get("token_endpoint")
    if not token_ep:
        raise OAuthError("Authorization server metadata lacks token_endpoint")
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": verifier,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(token_ep, data=data)
    if resp.status_code != 200:
        raise OAuthError(f"Token exchange failed: {resp.status_code} {resp.text[:300]}")
    return resp.json()


async def run_login_flow() -> dict:
    """Full interactive flow. Prints progress; returns the saved payload."""
    port = int(os.environ.get("SWIGGY_LYR_PORT", DEFAULT_PORT))
    redirect_uri = os.environ.get("SWIGGY_REDIRECT_URI") or f"http://localhost:{port}/callback"

    print("▸ Discovering Swiggy OAuth endpoints…")
    meta = await discover_authorization_server()
    client_id = await register_client(meta, redirect_uri)
    assert client_id  # register_client raises otherwise

    scope = meta.get("scopes_supported") and " ".join(meta["scopes_supported"]) or DEFAULT_SCOPE
    verifier, challenge = make_pkce_pair()
    state = secrets.token_urlsafe(16)

    auth_ep = meta.get("authorization_endpoint")
    if not auth_ep:
        raise OAuthError("Authorization server metadata lacks authorization_endpoint")
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scope,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    from urllib.parse import urlencode

    auth_url = f"{auth_ep}?{urlencode(params)}"

    print(f"▸ Opening browser for consent ({scope})…")
    print(f"  If it doesn't open, paste this URL into a browser:\n  {auth_url}")
    webbrowser.open(auth_url)

    print(f"▸ Listening on {redirect_uri} …")
    _CallbackState.query = None
    _CallbackState.done.clear()
    query = wait_for_callback(port)

    if "error" in query:
        desc = (query.get("error_description") or [""])[0]
        raise OAuthError(f"Authorization denied: {query['error'][0]} {desc}".strip())
    if not query.get("code"):
        raise OAuthError("OAuth redirect carried no authorization code")
    if query.get("state", [""])[0] != state:
        raise OAuthError("state mismatch — possible CSRF, aborting")

    print("▸ Exchanging code for Bearer token…")
    tokens = await exchange_code(meta, query["code"][0], verifier, client_id, redirect_uri)

    expires_in = tokens.get("expires_in")
    payload = {
        **tokens,
        "mode": "oauth",
        "client_id": client_id,
        "expires_at": int(time.time() + expires_in) if expires_in else None,
    }
    path = save_token(payload)
    print(f"▸ Saved → {path}")
    days = round((expires_in or 0) / 86400, 1)
    print(f"▸ Authenticated (valid ~{days} days)" if expires_in else "▸ Authenticated")
    return payload
