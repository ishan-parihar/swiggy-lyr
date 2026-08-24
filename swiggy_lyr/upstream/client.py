"""Streamable-HTTP MCP client for the three Swiggy upstream servers.

Every call opens a fresh MCP session with the stored Bearer token.
"""

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from swiggy_lyr.exceptions import (
    SwiggyLyrError,
    TokenExpiredError,
    UpstreamError,
)
from swiggy_lyr.logging_config import logger
from swiggy_lyr.session_state import get_bearer_token

INIT_TIMEOUT_S = 30


def _auth_headers() -> dict[str, str]:
    token, _ = get_bearer_token()
    return {"Authorization": f"Bearer {token}"}


async def _fresh_auth_headers() -> dict[str, str]:
    """Like _auth_headers but silently refreshes an expired stored token."""
    from swiggy_lyr.oauth import ensure_fresh_token

    return {"Authorization": f"Bearer {await ensure_fresh_token()}"}


# ponytail: fresh connection per call (~200ms overhead) — persistent session
# pool only if agent latency measurably matters.
async def list_stream_tools(url: str) -> list:
    """Return the upstream Tool definitions exposed by one stream."""
    headers = await _fresh_auth_headers()  # outside try: auth failures must stay typed
    try:
        async with streamablehttp_client(url, headers=headers, timeout=INIT_TIMEOUT_S) as (
            read,
            write,
            _get_session_id,
        ):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                return list(result.tools)
    except Exception as e:
        raise _translate(e, url) from e


async def call_stream_tool(url: str, tool_name: str, arguments: dict) -> dict:
    """Invoke one upstream tool; normalize the result to plain JSON."""
    headers = await _fresh_auth_headers()  # outside try: auth failures must stay typed
    try:
        async with streamablehttp_client(url, headers=headers, timeout=INIT_TIMEOUT_S) as (
            read,
            write,
            _get_session_id,
        ):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments or {})
                return _normalize(result)
    except SwiggyLyrError:
        raise
    except Exception as e:
        raise _translate(e, url) from e


def _normalize(result) -> dict:
    """Normalize an upstream CallToolResult; the error flag is never dropped.

    Upstream MCP servers commonly report tool-level failures as normal
    results with isError=true (sometimes WITH structuredContent) — surfacing
    is_error in both shapes keeps agent-side failures loud instead of
    masquerading as data.
    """
    is_error = bool(getattr(result, "isError", False))
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return {"data": structured, "is_error": is_error}
    texts = [c.text for c in (result.content or []) if hasattr(c, "text")]
    return {
        "data": "\n".join(texts),
        "is_error": is_error,
    }


def _iter_causes(e: BaseException):
    """Depth-first walk of an exception tree (ExceptionGroup-aware)."""
    yield e
    for sub in getattr(e, "exceptions", ()) or ():
        yield from _iter_causes(sub)


def _status_codes(causes: list[BaseException]) -> set[int]:
    """HTTP status codes carried by any exception in the tree."""
    codes: set[int] = set()
    for c in causes:
        sc = getattr(c, "status_code", None)
        if not isinstance(sc, int):
            response = getattr(c, "response", None)
            sc = getattr(response, "status_code", None)
        if isinstance(sc, int):
            codes.add(sc)
    return codes


def _translate(e: Exception, url: str) -> SwiggyLyrError:
    # The mcp SDK raises ExceptionGroups whose str() hides the real failure
    # ("unhandled errors in a TaskGroup") — always inspect the whole tree.
    causes = list(_iter_causes(e))
    joined = " | ".join(str(c) for c in causes)
    lowered = joined.lower()

    if (
        401 in _status_codes(causes)
        or "401" in lowered
        or "unauthorized" in lowered
        or "token expired" in lowered
    ):
        return TokenExpiredError(
            "Swiggy rejected our Bearer token",
            hint="Re-authenticate: swiggy-lyr --login",
        )
    if (
        any(isinstance(c, httpx.HTTPError) for c in causes)
        or "connect" in lowered
        or "timed out" in lowered
    ):
        return UpstreamError(f"Cannot reach {url}: {str(causes[-1])[:200]}")
    logger.debug("upstream raw error: %s", joined[:500])
    return UpstreamError(f"{url} failed: {str(causes[-1])[:300]}")
