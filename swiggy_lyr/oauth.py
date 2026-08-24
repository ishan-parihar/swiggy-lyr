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


async def discover_authorization_server(origin: str = MCP_ORIGIN, path: str = "") -> dict:
    """RFC 9728 discovery. Returns authorization-server metadata.

    Tries path-scoped protected-resource metadata first (MCP spec), then root
    protected-resource, then root auth-server metadata (Swiggy exposes this).
    """
    candidates = []
    if path:
        candidates.append(f"{origin}/.well-known/oauth-protected-resource{path}")
    candidates.append(f"{origin}/.well-known/oauth-protected-resource")
    candidates.append(f"{origin}/.well-known/oauth-authorization-server")

    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        resource_meta = None
        for url in candidates:
            try:
                resp = await client.get(url)
            except httpx.HTTPError as e:
                raise OAuthError(f"Cannot reach {origin}: {e}") from e
            if resp.status_code != 200:
                continue
            try:
                doc = resp.json()
            except ValueError as je:
                raise OAuthError(
                    f"Non-JSON response from {url} — upstream layout may have changed",
                ) from je
            # Root auth-server metadata found directly.
            if url.endswith("oauth-authorization-server"):
                return doc
            resource_meta = doc
            break

        servers = (resource_meta or {}).get("authorization_servers") or [f"{origin}/auth"]
        base = str(servers[0]).rstrip("/")
        parsed = urlparse(base)
        root = f"{parsed.scheme}://{parsed.netloc}"
        resp = await client.get(f"{root}/.well-known/oauth-authorization-server")
        if resp.status_code != 200:
            raise OAuthError(
                f"No authorization-server metadata at {root}",
                hint="Swiggy may have changed its OAuth layout — check their manifest repo.",
            )
        try:
            return resp.json()
        except ValueError as je:
            raise OAuthError(f"Non-JSON authorization-server metadata at {root}") from je


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
        "grant_types": ["authorization_code", "refresh_token"],
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
    raw_requests: list[str] = []


class _CallbackHandler(BaseHTTPRequestHandler):
    # Only requests on the redirect URI's path count as OAuth responses;
    # anything else (favicon, scanners, stray tabs) is noise.
    callback_path = "/callback"

    def do_GET(self):  # noqa: N802 (http.server API)
        _CallbackState.raw_requests.append(self.path)
        logger.info("OAuth callback hit: %s", self.path[:120])
        parsed = urlparse(self.path)
        if parsed.path != self.callback_path:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        query = parse_qs(parsed.query)
        if not query:
            # path match but empty params — treat as noise too
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        _CallbackState.query = query
        ok = "code" in query
        body = (
            b"<h2>swiggy-lyr</h2><p>Authorized. You can close this window.</p>"
            if ok
            else b"<h2>swiggy-lyr</h2><p>Authorization failed - check the terminal.</p>"
        )
        self.send_response(200 if ok else 400)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        _CallbackState.done.set()  # any real OAuth response ends the wait

    def log_message(self, format, *args):  # noqa: A002 (stdlib signature)
        pass


def validate_callback_query(query: dict[str, list[str]], expected_state: str) -> str:
    """Validate the OAuth redirect params; return the authorization code.

    Raises OAuthError on provider-denied flows, missing code, or state
    mismatch (possible CSRF).
    """
    if "error" in query:
        desc = (query.get("error_description") or [""])[0]
        raise OAuthError(f"Authorization denied: {query['error'][0]} {desc}".strip())
    code = (query.get("code") or [None])[0]
    if not code:
        raise OAuthError("OAuth redirect carried no authorization code")
    if (query.get("state") or [""])[0] != expected_state:
        raise OAuthError("state mismatch — possible CSRF, aborting")
    return code


def bind_callback_server(port: int, callback_path: str = "/callback") -> HTTPServer:
    """Bind the loopback callback server BEFORE the browser opens.

    Binding first guarantees that even an instantly-approved consent redirect
    finds a listening socket. Raises a typed OAuthError when the port is busy.
    """
    _CallbackHandler.callback_path = callback_path or "/callback"
    try:
        server = HTTPServer(("127.0.0.1", port), _CallbackHandler)
    except OSError as e:
        raise OAuthError(
            f"Cannot bind callback port {port}: {e}",
            hint="Port busy? Another login may be running — set SWIGGY_LYR_PORT "
            "or SWIGGY_REDIRECT_URI and retry.",
        ) from e
    server.timeout = 1
    return server


def serve_callback(server: HTTPServer, timeout: int = CALLBACK_TIMEOUT) -> dict[str, list[str]]:
    """Pump the bound server until the OAuth response arrives. Returns params."""
    deadline = time.time() + timeout
    try:
        while not _CallbackState.done.is_set():
            if time.time() > deadline:
                raise OAuthError("Timed out waiting for OAuth redirect")
            server.handle_request()
    finally:
        # snapshot BEFORE resetting state — the finally runs before return value is read
        query = _CallbackState.query or {}
        server.server_close()
        _CallbackState.done.clear()
        _CallbackState.query = None
    return query


def wait_for_callback(port: int, timeout: int = CALLBACK_TIMEOUT) -> dict[str, list[str]]:
    """Bind + pump in one call (bind/serve split exists for the login flow)."""
    return serve_callback(bind_callback_server(port), timeout)


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


async def try_refresh_stored_token() -> str | None:
    """Silently renew via refresh_token grant. Returns new access token or None.

    None means: no refresh token stored (manual/env tokens), or the server
    rejected the grant — caller should surface a re-login hint.
    """
    from swiggy_lyr.session_state import load_token, save_token

    payload = load_token() or {}
    refresh_token = payload.get("refresh_token")
    client_id = payload.get("client_id")
    if not refresh_token:
        return None
    if not client_id:
        # Refreshing with a guessed client_id would silently fail or, worse,
        # succeed against the wrong client — refuse and ask for re-login.
        logger.warning("Stored token has refresh_token but no client_id — cannot refresh")
        return None

    try:
        meta = await discover_authorization_server()
        token_ep = meta.get("token_endpoint")
        if not token_ep:
            return None
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                token_ep,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": client_id,
                },
            )
    except Exception as e:
        logger.warning("Token refresh failed: %s", e)
        return None
    if resp.status_code != 200:
        logger.warning("Token refresh rejected: %s %s", resp.status_code, resp.text[:150])
        return None

    tokens = resp.json()
    expires_in = tokens.get("expires_in")
    merged = {
        **payload,
        **tokens,
        "mode": "oauth",
        "expires_at": int(time.time() + expires_in) if expires_in else None,
    }
    save_token(merged)
    logger.info("Swiggy token refreshed silently")
    return merged["access_token"]


async def ensure_fresh_token() -> str:
    """Bearer token for requests; silent refresh when past expiry.

    Raises NotAuthenticatedError/TokenExpiredError when nothing can be renewed.
    """
    from swiggy_lyr.exceptions import TokenExpiredError
    from swiggy_lyr.session_state import get_bearer_token

    try:
        token, _ = get_bearer_token()
        return token
    except TokenExpiredError:
        refreshed = await try_refresh_stored_token()
        if refreshed is None:
            raise
        return refreshed


async def run_login_flow() -> dict:
    """Full interactive flow. Prints progress; returns the saved payload."""
    port = int(os.environ.get("SWIGGY_LYR_PORT", DEFAULT_PORT))
    redirect_uri = os.environ.get("SWIGGY_REDIRECT_URI") or f"http://localhost:{port}/callback"
    callback_path = urlparse(redirect_uri).path or "/callback"

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

    # Bind BEFORE opening the browser: an instantly-approved consent (returning
    # session) redirects within milliseconds and must find a listening socket.
    print(f"▸ Listening on {redirect_uri} …")
    _CallbackState.query = None
    _CallbackState.done.clear()
    server = bind_callback_server(port, callback_path)

    print(f"▸ Opening browser for consent ({scope})…")
    print(f"  If it doesn't open, paste this URL into a browser:\n  {auth_url}")
    webbrowser.open(auth_url)

    query = serve_callback(server)
    code = validate_callback_query(query, state)

    print("▸ Exchanging code for Bearer token…")
    tokens = await exchange_code(meta, code, verifier, client_id, redirect_uri)

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
