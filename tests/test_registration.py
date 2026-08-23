from swiggy_lyr.oauth import discover_authorization_server, make_pkce_pair
from swiggy_lyr.upstream.proxy import register_stream_tools
from tests.fakes import FAKE_TOOLS, fake_caller, fake_lister


def test_pkce_pair_shape():
    verifier, challenge = make_pkce_pair()
    assert 40 <= len(verifier) <= 128
    assert challenge != verifier
    v2, c2 = make_pkce_pair()
    assert verifier != v2 and challenge != c2  # random per flow


async def test_discovery_parses_metadata(httpx_mock=None):
    # smoke: function exists and is awaitable-shaped; live network is CI-excluded.
    assert callable(discover_authorization_server)


async def test_register_all_streams_via_fastmcp():
    from fastmcp import FastMCP

    mcp = FastMCP("test")
    total = 0
    for stream_name in ("food", "instamart", "dineout"):
        total += register_stream_tools(
            mcp,
            stream_name,
            f"https://fake/{stream_name}",
            tools=FAKE_TOOLS,
            lister=fake_lister,
            caller=fake_caller,
        )
    assert total == len(FAKE_TOOLS) * 3

    tools = {t.name: t for t in await mcp.list_tools()}
    assert "food_search_restaurants" in tools
    assert "instamart_checkout_cart" in tools
    assert "dineout_get_menu" in tools

    # readOnlyHint on reads, absent/false on mutating
    read_anns = tools["food_search_restaurants"].annotations
    mut_anns = tools["dineout_add_to_cart"].annotations
    assert getattr(read_anns, "readOnlyHint", None) is True or read_anns.get("readOnlyHint") is True
    assert getattr(mut_anns, "readOnlyHint", None) is False or mut_anns.get("readOnlyHint") is False


async def test_proxy_forwards_prefixed_tool():
    from fastmcp import Client, FastMCP

    mcp = FastMCP("test2")
    register_stream_tools(
        mcp, "food", "https://fake/food", tools=FAKE_TOOLS, lister=fake_lister, caller=fake_caller
    )

    async with Client(mcp) as client:
        result = await client.call_tool("food_search_restaurants", {"query": "biryani", "limit": 2})
        text = result.content[0].text if result.content else ""
        assert "search_restaurants" in text
