"""Edge-case matrix for the proxy layer: schemas, gating, normalization."""

import inspect

import pytest

from swiggy_lyr.exceptions import OrderSafetyError, TokenExpiredError, UpstreamError
from swiggy_lyr.upstream.client import _normalize, _translate
from swiggy_lyr.upstream.proxy import (
    build_signature,
    is_high_risk,
    is_mutating,
    make_tool,
    register_stream_tools,
)
from tests.fakes import fake_caller

# ── mutation classification matrix ───────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "mutating"),
    [
        # mutations — must be gated
        ("add_to_cart", True),
        ("food_add_item", True),
        ("update_quantity", True),
        ("remove_from_cart", True),
        ("delete_address", True),
        ("edit_cart_item", True),
        ("checkout_cart", True),
        ("place_order", True),
        ("submit_order", True),
        ("apply_coupon", True),
        ("cancel_order", True),
        ("book_table", True),
        ("customize_item", True),
        ("clear_cart", True),
        ("reorder_last", True),
        # reads — must stay ungated
        ("search_restaurants", False),
        ("get_menu", False),
        ("view_cart", False),  # contains "cart" but no mutation verb
        ("get_orders", False),
        ("track_order", False),
        ("check_availability", False),  # "check" ≠ "checkout"
        ("get_booking_details", False),  # over-block acceptable? no: read verb prefix
        ("restaurant_details", False),
    ],
)
def test_mutation_matrix(name, mutating):
    assert is_mutating(name) is mutating


@pytest.mark.parametrize(
    ("name", "risky"),
    [
        ("checkout", True),
        ("checkout_cart", True),
        ("instamart_place_order", True),
        ("place-order-v2", True),
        ("book_table", True),  # live finding: bookings are real-world commitments
        ("add_to_cart", False),
        ("cancel_order", False),
    ],
)
def test_high_risk_matrix(name, risky):
    assert is_high_risk(name) is risky


def test_get_booking_details_is_read():
    # regression guard: "booking" must not trip the "book" substring on reads
    assert not is_mutating("get_bookings")
    assert not is_mutating("list_bookings")
    # ...but the booking ACTION itself stays high-risk
    assert is_high_risk("book_table")
    assert is_mutating("book_table")


# ── build_signature edge cases ───────────────────────────────────────────


def test_signature_empty_schema():
    params = build_signature({})
    assert params == []


def test_signature_missing_type_becomes_any():
    from typing import Any

    schema = {"properties": {"weird": {}}, "required": ["weird"]}
    (p,) = build_signature(schema)
    assert p.name == "weird"
    assert p.annotation is Any
    assert p.default is inspect.Parameter.empty


def test_signature_all_json_types():
    schema = {
        "properties": {
            "s": {"type": "string"},
            "n": {"type": "number"},
            "i": {"type": "integer"},
            "b": {"type": "boolean"},
            "a": {"type": "array"},
            "o": {"type": "object"},
        },
        "required": ["s"],
    }
    by_name = {p.name: p for p in build_signature(schema)}
    assert by_name["n"].annotation == float | None
    assert by_name["i"].annotation == int | None
    assert by_name["b"].annotation == bool | None
    assert by_name["a"].annotation == list | None
    assert by_name["o"].annotation == dict | None
    assert by_name["s"].default is inspect.Parameter.empty
    assert by_name["s"].annotation is str
    for opt in ("n", "i", "b", "a", "o"):
        assert by_name[opt].default is None


async def test_extra_kwargs_pass_through():
    """Upstream adds a field mid-flight; proxy must forward unknown kwargs."""
    fn = make_tool("search", "", {"type": "object", "properties": {}}, "https://x", fake_caller)
    out = await fn(surprise_new_param="v")  # type: ignore[call-arg]
    assert out["data"]["echo_args"] == {"surprise_new_param": "v"}


# ── registration edge cases ──────────────────────────────────────────────


async def test_dict_shaped_tools_and_missing_fields(caplog):
    from fastmcp import FastMCP

    mcp = FastMCP("edge")
    tools = [
        {"name": "plain_dict_tool"},  # no description/schema
        {"name": "full", "description": "d", "inputSchema": {"type": "object", "properties": {}}},
        {"no_name_here": True},  # unnamed → skipped with warning
    ]
    count = register_stream_tools(mcp, "food", "https://x", tools=tools, caller=fake_caller)
    assert count == 2
    names = {t.name for t in await mcp.list_tools()}
    assert names == {"food_plain_dict_tool", "food_full"}


async def test_lister_failure_returns_zero_not_crash():
    from fastmcp import FastMCP

    async def bad_lister(url):
        raise UpstreamError("down")

    mcp = FastMCP("edge2")
    assert (
        register_stream_tools(mcp, "dineout", "https://x", lister=bad_lister, caller=fake_caller)
        == 0
    )


# ── client result normalization / error translation ─────────────────────


class _Content:
    def __init__(self, text=""):
        self.text = text


class _Result:
    def __init__(self, structured=None, content=None, is_error=False):
        self.structuredContent = structured
        self.content = content or []
        self.isError = is_error


def test_normalize_prefers_structured():
    r = _Result(structured={"a": 1}, content=[_Content("ignored")])
    assert _normalize(r) == {"data": {"a": 1}}


def test_normalize_joins_texts_and_error_flag():
    r = _Result(content=[_Content("line1"), _Content("line2")], is_error=True)
    out = _normalize(r)
    assert out == {"data": "line1\nline2", "is_error": True}


def test_normalize_skips_non_text_content():
    class Img:
        pass

    r = _Result(content=[Img(), _Content("ok")])
    assert _normalize(r)["data"] == "ok"


def test_translate_401_variants():
    for exc in [Exception("HTTP 401"), Exception("Unauthorized"), Exception("token expired")]:
        err = _translate(exc, "https://x")
        assert isinstance(err, TokenExpiredError)
        assert "--login" in err.hint


def test_translate_transport_errors():
    err = _translate(ConnectionError("connection refused"), "https://x")
    assert isinstance(err, UpstreamError)


def test_translate_generic_wraps():
    err = _translate(RuntimeError("boom"), "https://x")
    assert isinstance(err, UpstreamError)
    assert "boom" in str(err)


# ── safety gate through generated wrappers ───────────────────────────────


async def test_gate_message_carries_hint(monkeypatch):
    monkeypatch.delenv("SWIGGY_LYR_ALLOW_ORDERS", raising=False)
    schema = {
        "type": "object",
        "properties": {"item_id": {"type": "string"}},
        "required": ["item_id"],
    }
    fn = make_tool("add_to_cart", "", schema, "https://x", fake_caller)
    with pytest.raises(OrderSafetyError) as ei:
        await fn(item_id="x")
    assert "SWIGGY_LYR_ALLOW_ORDERS=1" in str(ei.value)


async def test_checkout_hint_mentions_confirm(monkeypatch):
    monkeypatch.setenv("SWIGGY_LYR_ALLOW_ORDERS", "1")
    fn = make_tool(
        "checkout_cart", "", {"type": "object", "properties": {}}, "https://x", fake_caller
    )
    with pytest.raises(OrderSafetyError) as ei:
        await fn()
    assert "confirm=true" in str(ei.value)


async def test_confirm_false_is_still_blocked(monkeypatch):
    monkeypatch.setenv("SWIGGY_LYR_ALLOW_ORDERS", "1")
    fn = make_tool(
        "checkout_cart", "", {"type": "object", "properties": {}}, "https://x", fake_caller
    )
    with pytest.raises(OrderSafetyError):
        await fn(confirm=False)
