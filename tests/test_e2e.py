"""End-to-end through the real FastMCP stack (in-memory transport, fakes upstream)."""

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from swiggy_lyr.upstream.proxy import register_stream_tools
from tests.fakes import FAKE_TOOLS, fake_caller


def _text(result) -> str:
    return "\n".join(getattr(c, "text", "") for c in (result.content or []))


def _build_server() -> FastMCP:
    mcp = FastMCP("e2e")
    for stream in ("food", "instamart", "dineout"):
        register_stream_tools(
            mcp,
            stream,
            f"https://fake/{stream}",
            tools=FAKE_TOOLS,
            lister=None,
            caller=fake_caller,
        )
    return mcp


async def test_read_tool_end_to_end():
    async with Client(_build_server()) as client:
        result = await client.call_tool("food_search_restaurants", {"query": "dosa"})
        assert not result.is_error
        assert "search_restaurants" in _text(result)


async def test_list_tools_shows_all_streams_prefixed():
    async with Client(_build_server()) as client:
        tools = await client.list_tools()
        names = {t.name for t in tools}
        assert len(names) == len(FAKE_TOOLS) * 3
        assert any(n.startswith("food_") for n in names)
        assert any(n.startswith("instamart_") for n in names)
        assert any(n.startswith("dineout_") for n in names)


async def test_mutating_tool_blocked_through_mcp_protocol(monkeypatch):
    monkeypatch.delenv("SWIGGY_LYR_ALLOW_ORDERS", raising=False)
    async with Client(_build_server()) as client:
        # gate violation surfaces as a loud MCP error
        with pytest.raises(ToolError, match="ALLOW_ORDERS"):
            await client.call_tool("food_add_to_cart", {"item_id": "x"})

        # server still healthy afterwards — session survives the error
        ok = await client.call_tool("instamart_get_menu", {"restaurant_id": "r1"})
        assert not ok.is_error


async def test_high_risk_needs_confirm_through_mcp(monkeypatch):
    monkeypatch.setenv("SWIGGY_LYR_ALLOW_ORDERS", "1")
    async with Client(_build_server()) as client:
        with pytest.raises(ToolError, match="confirm"):
            await client.call_tool("food_checkout_cart", {"address_id": "a"})

        allowed = await client.call_tool("food_checkout_cart", {"address_id": "a", "confirm": True})
        assert not allowed.is_error
        assert "checkout_cart" in _text(allowed)


async def test_gate_hint_embedded_in_error_text(monkeypatch):
    """Agents only see message text — hints must survive into it."""
    monkeypatch.delenv("SWIGGY_LYR_ALLOW_ORDERS", raising=False)
    async with Client(_build_server()) as client:
        with pytest.raises(ToolError) as ei:
            await client.call_tool("dineout_add_to_cart", {"item_id": "r"})
        assert "SWIGGY_LYR_ALLOW_ORDERS=1" in str(ei.value)


async def test_schema_exposed_to_clients_has_required_fields():
    from swiggy_lyr.upstream.proxy import make_tool

    mcp = FastMCP("schema")
    schema = {
        "type": "object",
        "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
        "required": ["query"],
    }
    mcp.tool(name="x_search")(make_tool("search_restaurants", "", schema, "https://x", fake_caller))
    async with Client(mcp) as client:
        tools = await client.list_tools()
        (tool,) = tools
        input_schema = tool.inputSchema
        assert set(input_schema["properties"]) == {"query", "limit"}
        assert input_schema["required"] == ["query"]


async def test_readonly_hint_annotation_reaches_protocol():
    async with Client(_build_server()) as client:
        tools = {t.name: t for t in await client.list_tools()}
        read = tools["dineout_search_restaurants"]
        mutate = tools["dineout_add_to_cart"]
        high = tools["food_checkout_cart"]

        def ro(t):
            anns = t.annotations
            return bool(getattr(anns, "readOnlyHint", None)) if anns else False

        assert ro(read) is True
        assert ro(mutate) is False
        assert ro(high) is False
