import pytest

from swiggy_lyr.exceptions import OrderSafetyError
from swiggy_lyr.upstream.proxy import (
    build_signature,
    is_high_risk,
    is_mutating,
    make_tool,
)
from tests.fakes import FAKE_TOOLS, fake_caller


def test_mutating_detection():
    assert is_mutating("checkout_cart")
    assert is_mutating("place_order")
    assert is_mutating("book_table")
    assert not is_mutating("search_restaurants")
    assert not is_mutating("get_menu")


def test_high_risk_detection():
    assert is_high_risk("checkout_cart")
    assert is_high_risk("instamart_place_order")
    assert not is_high_risk("add_to_cart")
    assert not is_high_risk("search_products")


def test_build_signature_required_vs_optional():
    schema = FAKE_TOOLS[0].inputSchema
    params = build_signature(schema)
    by_name = {p.name: p for p in params}
    assert "query" in by_name
    assert "limit" in by_name
    assert by_name["query"].default is inspect_empty()
    assert by_name["limit"].default is None


def inspect_empty():
    import inspect

    return inspect.Parameter.empty


async def test_read_tool_forwards(monkeypatch):
    monkeypatch.delenv("SWIGGY_LYR_ALLOW_ORDERS", raising=False)
    fn = make_tool(
        "search_restaurants", "Search", FAKE_TOOLS[0].inputSchema, "https://x", fake_caller
    )
    out = await fn(query="dosa", limit=3)
    assert out["data"]["echo_tool"] == "search_restaurants"
    assert out["data"]["echo_args"] == {"query": "dosa", "limit": 3}


async def test_mutating_blocked_without_env(monkeypatch):
    monkeypatch.delenv("SWIGGY_LYR_ALLOW_ORDERS", raising=False)
    fn = make_tool("add_to_cart", "Add", FAKE_TOOLS[2].inputSchema, "https://x", fake_caller)
    with pytest.raises(OrderSafetyError):
        await fn(item_id="i1")


async def test_mutating_allowed_with_env(monkeypatch):
    monkeypatch.setenv("SWIGGY_LYR_ALLOW_ORDERS", "1")
    fn = make_tool("add_to_cart", "Add", FAKE_TOOLS[2].inputSchema, "https://x", fake_caller)
    out = await fn(item_id="i1", qty=2)
    assert out["data"]["echo_args"] == {"item_id": "i1", "qty": 2}


async def test_checkout_needs_env_and_confirm(monkeypatch):
    monkeypatch.delenv("SWIGGY_LYR_ALLOW_ORDERS", raising=False)
    fn = make_tool("checkout_cart", "Checkout", FAKE_TOOLS[3].inputSchema, "https://x", fake_caller)

    with pytest.raises(OrderSafetyError):  # no env at all
        await fn(address_id="a1")

    monkeypatch.setenv("SWIGGY_LYR_ALLOW_ORDERS", "1")
    with pytest.raises(OrderSafetyError):  # env set but no confirm
        await fn(address_id="a1")

    out = await fn(address_id="a1", confirm=True)
    assert out["data"]["echo_tool"] == "checkout_cart"


async def test_confirm_param_only_on_high_risk():
    import inspect

    safe_fn = make_tool("add_to_cart", "", FAKE_TOOLS[2].inputSchema, "https://x", fake_caller)
    risky_fn = make_tool("checkout_cart", "", FAKE_TOOLS[3].inputSchema, "https://x", fake_caller)
    assert "confirm" not in inspect.signature(safe_fn).parameters
    assert "confirm" in inspect.signature(risky_fn).parameters
