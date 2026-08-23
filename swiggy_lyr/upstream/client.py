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


# ponytail: fresh connection per call (~200ms overhead) — persistent session
# pool only if agent latency measurably matters.
async def list_stream_tools(url: str) -> list:
    """Return the upstream Tool definitions exposed by one stream."""
    headers = _auth_headers()  # outside try: auth failures must stay typed
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
    headers = _auth_headers()  # outside try: auth failures must stay typed
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
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return {"data": structured}
    texts = [c.text for c in (result.content or []) if hasattr(c, "text")]
    return {
        "data": "\n".join(texts),
        "is_error": bool(getattr(result, "isError", False)),
    }


def _translate(e: Exception, url: str) -> SwiggyLyrError:
    msg = str(e)
    lowered = msg.lower()
    if "401" in msg or "unauthorized" in lowered or "token expired" in lowered:
        return TokenExpiredError(
            "Swiggy rejected our Bearer token",
            hint="Re-authenticate: swiggy-lyr --login",
        )
    if isinstance(e, httpx.HTTPError) or "connect" in lowered or "timed out" in lowered:
        return UpstreamError(f"Cannot reach {url}: {msg[:200]}")
    logger.debug("upstream raw error: %s", msg)
    return UpstreamError(f"{url} failed: {msg[:300]}")
