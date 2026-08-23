"""Dynamic tool proxying: upstream tools/list → local FastMCP registrations.

Schemas are generated from the upstream inputSchema at startup, so Swiggy
schema changes propagate without code changes here. Mutating tools are gated
behind SWIGGY_LYR_ALLOW_ORDERS=1; checkout/place_order additionally need
confirm=true because COD orders cannot be cancelled.
"""

import inspect
import os
from typing import Any

from fastmcp import FastMCP

from swiggy_lyr.exceptions import OrderSafetyError, UpstreamError
from swiggy_lyr.logging_config import logger
from swiggy_lyr.upstream.streams import STREAMS, TOOL_PREFIX, Caller, Lister

JSON_TYPE_MAP: dict[str, Any] = {
    "string": str,
    "number": float,
    "integer": int,
    "boolean": bool,
    "array": list,
    "object": dict,
}

# Substring match on the *upstream* tool name.
# ponytail: verb-substring heuristic, biased to over-block (a mis-flagged read
# just needs ALLOW_ORDERS=1; a missed mutation would spend real money).
_MUTATING = (
    "add",
    "update",
    "remove",
    "delete",
    "edit",
    "checkout",
    "place_order",
    "submit",
    "apply",
    "cancel",
    "book",
    "customize",
    "clear",
    "reorder",
)
_HIGH_RISK = ("checkout", "place_order", "place-order")

# Leading read verbs win outright: get_bookings / view_cart are reads even
# though they contain gated substrings ("book", …).
_READ_VERBS = frozenset({"get", "list", "view", "search", "browse", "track", "fetch", "check"})


def is_mutating(tool_name: str) -> bool:
    lowered = tool_name.lower()
    if lowered.split("_", 1)[0] in _READ_VERBS:
        return False
    return any(k in lowered for k in _MUTATING)


def is_high_risk(tool_name: str) -> bool:
    lowered = tool_name.lower()
    return any(k in lowered for k in _HIGH_RISK)


def _orders_allowed() -> bool:
    return os.environ.get("SWIGGY_LYR_ALLOW_ORDERS") == "1"


def build_signature(input_schema: dict) -> list[inspect.Parameter]:
    """Map upstream JSON Schema properties to a Python inspect.Signature."""
    props = (input_schema or {}).get("properties") or {}
    required = set((input_schema or {}).get("required") or [])
    params = []
    for pname, pdef in props.items():
        jtype = pdef.get("type")
        ann = JSON_TYPE_MAP.get(jtype, Any)
        if isinstance(ann, type) and pname not in required:
            ann = ann | None
        default = inspect.Parameter.empty if pname in required else None
        params.append(
            inspect.Parameter(
                pname, inspect.Parameter.KEYWORD_ONLY, annotation=ann, default=default
            )
        )
    return params


def make_tool(upstream_name: str, description: str, input_schema: dict, url: str, caller: Caller):
    """Build the async wrapper FastMCP will register."""
    params = build_signature(input_schema)
    mutating = is_mutating(upstream_name)
    high_risk = is_high_risk(upstream_name)

    # confirm param only exists on tools that can spend real money.
    if high_risk:
        params.append(
            inspect.Parameter(
                "confirm", inspect.Parameter.KEYWORD_ONLY, annotation=bool, default=False
            )
        )

    sig = inspect.Signature(params)
    annotations = {p.name: p.annotation for p in params}
    annotations["return"] = dict

    async def _impl(**kwargs) -> dict:
        confirm = bool(kwargs.pop("confirm", False))
        if high_risk:
            if not _orders_allowed():
                raise OrderSafetyError(
                    f"{upstream_name} can place a non-cancellable COD order",
                    hint="Set SWIGGY_LYR_ALLOW_ORDERS=1 to enable ordering",
                )
            if not confirm:
                raise OrderSafetyError(
                    f"{upstream_name} blocked — no explicit confirmation",
                    hint="Re-call with confirm=true after reviewing the cart",
                )
        elif mutating and not _orders_allowed():
            raise OrderSafetyError(
                f"{upstream_name} mutates your Swiggy account",
                hint="Set SWIGGY_LYR_ALLOW_ORDERS=1 to enable cart/booking mutations",
            )
        return await caller(url, upstream_name, kwargs)

    _impl.__signature__ = sig  # type: ignore[attr-defined]
    _impl.__annotations__ = annotations
    _impl.__name__ = upstream_name
    _impl.__qualname__ = upstream_name
    _impl.__doc__ = description or f"Proxy for upstream tool {upstream_name}"
    return _impl


def _run_discovery(coro):
    """Await a discovery coroutine from sync context, loop or no loop."""
    import asyncio
    import concurrent.futures

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Already inside an event loop (e.g. server built lazily) — bridge via thread.
    # ponytail: one-shot thread per stream startup; a shared executor only if
    # this ever runs hot.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def register_stream_tools(
    mcp: FastMCP,
    stream_name: str,
    url: str,
    tools: list | None = None,
    lister: Lister | None = None,
    caller: Caller | None = None,
) -> int:
    """Register one stream's tools. Returns count registered.

    `tools` bypasses discovery (tests / offline). On discovery failure the
    stream is skipped with a warning so other streams still serve.
    """
    from swiggy_lyr.upstream.client import call_stream_tool, list_stream_tools

    caller = caller or call_stream_tool
    if tools is None:
        try:
            tools = _run_discovery((lister or list_stream_tools)(url))
        except Exception as e:
            logger.warning("Stream %s unavailable (%s) — skipped", stream_name, e)
            return 0

    prefix = TOOL_PREFIX[stream_name]
    registered = 0
    for t in tools:
        name = getattr(t, "name", None) or (t.get("name") if isinstance(t, dict) else None)
        if not name:
            logger.warning("Stream %s has an unnamed tool — skipped", stream_name)
            continue
        desc = getattr(t, "description", None) or (
            t.get("description") if isinstance(t, dict) else ""
        )
        schema = getattr(t, "inputSchema", None)
        if schema is None and isinstance(t, dict):
            schema = t.get("inputSchema")

        fn = make_tool(
            name, str(desc or ""), schema or {"type": "object", "properties": {}}, url, caller
        )
        decorator = mcp.tool(
            name=f"{prefix}{name}",
            annotations={"readOnlyHint": not is_mutating(name), "openWorldHint": True},
        )
        decorator(fn)
        registered += 1
    return registered


def register_all_streams(mcp: FastMCP) -> dict[str, int]:
    counts = {}
    for stream_name, url in STREAMS.items():
        counts[stream_name] = register_stream_tools(mcp, stream_name, url)
    total = sum(counts.values())
    logger.info("swiggy-lyr registered %d tools %s", total, counts)
    if total == 0:
        raise UpstreamError(
            "No streams reachable — check auth",
            hint="Run swiggy-lyr --login first; then swiggy-lyr --status",
        )
    return counts
