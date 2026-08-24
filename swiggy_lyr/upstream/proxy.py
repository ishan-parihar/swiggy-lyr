"""Dynamic tool proxying: upstream tools/list → local FastMCP registrations.

Schemas are generated from the upstream inputSchema at startup, so Swiggy
schema changes propagate without code changes here. Mutating tools are gated
behind SWIGGY_LYR_ALLOW_ORDERS=1; checkout/place_order additionally need
confirm=true because COD orders cannot be cancelled.

Hardening (live-verified failure modes):
- Upstream property names are sanitized to valid Python parameter names and
  mapped back to the originals before the upstream call — a hyphenated or
  reserved-word param can no longer crash registration.
- A tool that fails to register is skipped with an error log; its stream
  and the other streams keep serving.
- Omitted optional arguments are never forwarded as explicit nulls; schema
  defaults are applied instead.
"""

import asyncio
import inspect
import keyword
import os
import re
from typing import Annotated, Any, Literal, get_origin

from fastmcp import FastMCP
from pydantic import Field

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
# Verbs below are derived from Swiggy's LIVE tool inventory (Aug 2026):
# place_food_order, *_confirm_order, flush_food_cart, create_cart, checkout…
_MUTATING = (
    "add",
    "update",
    "remove",
    "delete",
    "edit",
    "checkout",
    "place",
    "submit",
    "apply",
    "cancel",
    "book",
    "customize",
    "clear",
    "flush",
    "create",
    "confirm",
    "reorder",
)
_HIGH_RISK = (
    "checkout",
    "confirm",
    "place",
    # free-tier Dineout reservations are still real-world commitments
    # (a table gets held) — live finding: book_table passed layer-1 alone.
    "book",
)

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


def safe_param_name(name: str, used: set[str]) -> str:
    """Map any upstream property name onto a unique valid Python identifier."""
    cleaned = re.sub(r"\W", "_", str(name)) or "param"
    if cleaned[0].isdigit():
        cleaned = f"param_{cleaned}"
    if keyword.iskeyword(cleaned):
        cleaned = f"{cleaned}_"
    candidate = cleaned
    i = 2
    while candidate in used:
        candidate = f"{cleaned}_{i}"
        i += 1
    used.add(candidate)
    return candidate


def _annotation_for(pdef: dict) -> Any:
    """Best-effort Python annotation from one JSON-Schema property def."""
    enum = pdef.get("enum")

    jtype = pdef.get("type")
    if isinstance(jtype, list):
        non_null = [JSON_TYPE_MAP[t] for t in jtype if t != "null"]
        nullable = "null" in jtype
        base = non_null[0] if len(non_null) == 1 else Any
    elif jtype is not None:
        base = JSON_TYPE_MAP.get(jtype, Any)
        nullable = bool(pdef.get("nullable"))
    elif "anyOf" in pdef:
        subs = pdef["anyOf"]
        types = [JSON_TYPE_MAP.get(s.get("type")) for s in subs]
        known = [t for t in types if t]
        nullable = any(s.get("type") == "null" for s in subs)
        base = known[0] if len(known) == 1 else Any
    else:
        base, nullable = Any, False

    if isinstance(enum, list) and enum:
        try:
            base = Literal[tuple(enum)]
        except TypeError:
            pass  # unhashable enum members — fall back to the plain type

    ann: Any = base
    if nullable:
        ann = ann | None
    return ann


def _optionalize(ann: Any) -> Any:
    """Optional parameters accept an explicit null (dropped before upstream)."""
    if isinstance(ann, type) or get_origin(ann) is not None:
        return ann | None
    return ann  # bare Any already accepts null


def build_signature(input_schema: dict) -> list[inspect.Parameter]:
    """Backwards-compatible view: just the parameters."""
    return _build_params(input_schema)[0]


def _build_params(
    input_schema: dict,
) -> tuple[list[inspect.Parameter], dict[str, str], bool]:
    """Map upstream JSON Schema properties to Python parameters.

    Returns (params, aliases, native_confirm):
    - aliases maps sanitized parameter name → original upstream property name
      (identity when the name was already valid),
    - native_confirm is True when the upstream schema itself defines a
      "confirm" property (we must not shadow it).
    """
    props = (input_schema or {}).get("properties") or {}
    required = set((input_schema or {}).get("required") or [])
    params: list[inspect.Parameter] = []
    aliases: dict[str, str] = {}
    used: set[str] = set()
    for pname, pdef in props.items():
        pdef = pdef if isinstance(pdef, dict) else {}
        safe = safe_param_name(pname, used)
        aliases[safe] = pname

        ann = _annotation_for(pdef)
        if pname not in required:
            ann = _optionalize(ann)
        if pdef.get("description"):
            # carry upstream parameter docs through to the exposed schema
            ann = Annotated[ann, Field(description=str(pdef["description"]))]

        default = inspect.Parameter.empty if pname in required else pdef.get("default", None)
        params.append(
            inspect.Parameter(safe, inspect.Parameter.KEYWORD_ONLY, annotation=ann, default=default)
        )
    native_confirm = "confirm" in props
    return params, aliases, native_confirm


def make_tool(upstream_name: str, description: str, input_schema: dict, url: str, caller: Caller):
    """Build the async wrapper FastMCP will register."""
    params, aliases, native_confirm = _build_params(input_schema)
    mutating = is_mutating(upstream_name)
    high_risk = is_high_risk(upstream_name)

    # Synthetic confirm param only when the upstream schema doesn't already
    # define one (shadowing it would crash Signature construction).
    if high_risk and not native_confirm:
        params.append(
            inspect.Parameter(
                "confirm", inspect.Parameter.KEYWORD_ONLY, annotation=bool, default=False
            )
        )

    sig = inspect.Signature(params)
    annotations = {p.name: p.annotation for p in params}
    annotations["return"] = dict

    async def _impl(**kwargs) -> dict:
        # translate sanitized names back to the upstream property names
        kwargs = {aliases.get(k, k): v for k, v in kwargs.items()}

        if high_risk:
            if native_confirm:
                confirm = bool(kwargs.get("confirm"))
            else:
                confirm = bool(kwargs.pop("confirm", False))
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

        # Never forward omitted optionals as explicit nulls — many upstream
        # handlers treat {"limit": null} differently from an absent key.
        payload = {k: v for k, v in kwargs.items() if v is not None}
        return await caller(url, upstream_name, payload)

    _impl.__signature__ = sig  # type: ignore[attr-defined]
    _impl.__annotations__ = annotations
    _impl.__name__ = upstream_name
    _impl.__qualname__ = upstream_name
    _impl.__doc__ = description or f"Proxy for upstream tool {upstream_name}"
    return _impl


def _run_discovery(coro):
    """Await a discovery coroutine from sync context, loop or no loop."""
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


async def discover_streams(
    streams: dict[str, str], lister: Lister | None = None
) -> dict[str, list]:
    """Discover every stream's tools concurrently.

    Failures degrade to an empty list for that stream (logged at ERROR) so one
    downed stream never blocks or kills the others. Order mirrors `streams`.
    """
    from swiggy_lyr.upstream.client import list_stream_tools

    fn = lister or list_stream_tools
    names = list(streams)
    results = await asyncio.gather(*(fn(streams[name]) for name in names), return_exceptions=True)
    out: dict[str, list] = {}
    for name, res in zip(names, results, strict=True):
        if isinstance(res, BaseException):
            logger.error("Stream %s discovery failed (%s) — serving without this stream", name, res)
            out[name] = []
        else:
            out[name] = res
    return out


def register_stream_tools(
    mcp: FastMCP,
    stream_name: str,
    url: str,
    tools: list | None = None,
    lister: Lister | None = None,
    caller: Caller | None = None,
) -> int:
    """Register one stream's tools. Returns count registered.

    `tools` bypasses discovery (tests / pre-discovery). `tools=[]` registers
    nothing without attempting discovery (pre-discovery reported failure).
    A tool that cannot be wrapped is skipped with an error log — one weird
    upstream tool must never take down the stream or the server.
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

        try:
            fn = make_tool(
                name, str(desc or ""), schema or {"type": "object", "properties": {}}, url, caller
            )
            decorator = mcp.tool(
                name=f"{prefix}{name}",
                annotations={"readOnlyHint": not is_mutating(name), "openWorldHint": True},
            )
            decorator(fn)
        except Exception as e:
            logger.error(
                "Stream %s: tool %r could not be proxied and was skipped (%s)",
                stream_name,
                name,
                e,
            )
            continue
        registered += 1
    return registered


def register_all_streams(mcp: FastMCP) -> dict[str, int]:
    """Discover all streams in parallel, then register each stream's tools."""
    discovered = _run_discovery(discover_streams(STREAMS))
    counts = {
        name: register_stream_tools(mcp, name, url, tools=discovered.get(name))
        for name, url in STREAMS.items()
    }
    total = sum(counts.values())
    logger.info("swiggy-lyr registered %d tools %s", total, counts)
    if total == 0:
        raise UpstreamError(
            "No streams reachable — check auth",
            hint="Run swiggy-lyr --login first; then swiggy-lyr --status",
        )
    return counts
