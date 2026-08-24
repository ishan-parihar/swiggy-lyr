"""Regression fixtures from Swiggy's LIVE tool inventory (Aug 2026).

Every name here is a real upstream tool observed in production via
tests/live_verify.py. If Swiggy renames tools, update this file — these
classifications guard real money.
"""

import pytest

from swiggy_lyr.upstream.proxy import is_high_risk, is_mutating

FOOD_TOOLS = [
    "apply_food_coupon",
    "check_payment_status",
    "confirm_order",
    "fetch_food_coupons",
    "flush_food_cart",
    "get_addresses",
    "get_food_cart",
    "get_food_delivery_status",
    "get_food_order_details",
    "get_food_orders",
    "get_payment_options",
    "get_restaurant_menu",
    "place_food_order",
    "report_error",
    "search_menu",
    "search_restaurants",
    "track_food_order",
    "update_food_cart",
]

INSTAMART_TOOLS = [
    "check_payment_status",
    "checkout",
    "clear_cart",
    "confirm_order",
    "get_addresses",
    "get_cart",
    "get_delivery_status",
    "get_orders",
    "get_payment_options",
    "report_error",
    "search_products",
    "track_order",
    "update_cart",
    "your_go_to_items",
]

DINEOUT_TOOLS = [
    "book_table",
    "check_payment_status",
    "confirm_order",
    "create_cart",
    "get_available_slots",
    "get_booking_status",
    "get_payment_options",
    "get_restaurant_details",
    "get_saved_locations",
    "render_restaurants_dineout",
    "report_error",
    "search_restaurants_dineout",
]

ALL_LIVE = FOOD_TOOLS + INSTAMART_TOOLS + DINEOUT_TOOLS

MUST_BE_GATED = [
    # money-touching: env + (for high-risk) confirm=true required
    *{n for n in ALL_LIVE if is_high_risk(n)},
]

MUTATING_LIVE = {
    "apply_food_coupon",
    "flush_food_cart",
    "place_food_order",
    "confirm_order",  # all three streams
    "update_food_cart",
    "checkout",
    "clear_cart",
    "update_cart",
    "book_table",
    "create_cart",
}


def test_every_live_tool_classified_consistently():
    """A tool is either gated or read; reads must carry a known read verb."""
    for n in ALL_LIVE:
        mutating = is_mutating(n)
        if n in MUTATING_LIVE:
            assert mutating, f"{n} must be gated"
        else:
            assert not mutating, f"{n} unexpectedly gated (over-block)"


@pytest.mark.parametrize(
    "name",
    ["confirm_order", "place_food_order", "checkout"],  # the catastrophic trio
)
def test_money_tools_are_high_risk(name):
    assert is_high_risk(name)
    assert is_mutating(name)


@pytest.mark.parametrize("name", sorted(MUTATING_LIVE))
def test_all_live_mutations_gated(name):
    assert is_mutating(name), f"REGRESSION: {name} escaped the gate"


@pytest.mark.parametrize("name", sorted(set(ALL_LIVE) - MUTATING_LIVE))
def test_all_live_reads_ungated(name):
    assert not is_mutating(name), f"{name} misclassified as mutating"


def test_read_verbs_win_even_with_mutation_substrings():
    """track_order / get_bookings contain 'order'/'book' but are reads."""
    assert not is_mutating("track_order")
    assert not is_mutating("get_booking_status")
    assert not is_mutating("get_food_orders")


# ── documented-inventory drift guard (README / SKILL.md claim 44 tools) ──


def test_documented_counts_match_fixtures():
    """README + SKILL.md advertise food=18, instamart=14, dineout=12 (44 total).

    If a live recount changes these numbers, update the fixtures AND the docs
    in the same commit — this test exists so they can never silently diverge.
    """
    assert len(FOOD_TOOLS) == 18
    assert len(INSTAMART_TOOLS) == 14
    assert len(DINEOUT_TOOLS) == 12
    assert sum(len(g) for g in (FOOD_TOOLS, INSTAMART_TOOLS, DINEOUT_TOOLS)) == 44
    # cross-stream name sharing is expected (tools get prefixed per stream),
    # but a stream must never list the same tool twice
    for group in (FOOD_TOOLS, INSTAMART_TOOLS, DINEOUT_TOOLS):
        assert len(group) == len(set(group))


def test_dineout_search_is_a_read():
    # regression: search_restaurants_dineout was missing from the fixture set
    assert not is_mutating("search_restaurants_dineout")
    assert not is_high_risk("search_restaurants_dineout")
