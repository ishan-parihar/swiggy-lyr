---
name: swiggy-mcp
description: >
  Order food, groceries, and book tables via Swiggy through MCP. Aggregates
  Swiggy's official Food, Instamart, and Dineout streams behind one local
  server with OAuth auth and safety-gated ordering.
---

# Swiggy MCP Skill

Unified Swiggy access for agents — restaurant search and food ordering,
Instamart groceries, and Dineout table reservations.

## Quick Start

```bash
# Authenticate once (browser consent; ~5 day token)
swiggy-lyr --login

# Run the MCP server
swiggy-lyr
```

## MCP Configuration

```json
{
  "mcpServers": {
    "swiggy": {
      "command": "swiggy-lyr",
      "args": []
    }
  }
}
```

## Tool Families

Tools are discovered from the upstream streams at startup and prefixed.
Live inventory (44 tools, Aug 2026):

| Prefix | Count | Tools |
|--------|-------|-------|
| `food_` | 18 | search_restaurants, search_menu, get_restaurant_menu, get_food_cart, update_food_cart, flush_food_cart, apply_food_coupon, fetch_food_coupons, place_food_order, confirm_order, get_food_orders, get_food_order_details, track_food_order, get_food_delivery_status, get_addresses, get_payment_options, check_payment_status, report_error |
| `instamart_` | 14 | search_products, your_go_to_items, get_cart, update_cart, clear_cart, checkout, confirm_order, get_orders, track_order, get_delivery_status, get_addresses, get_payment_options, check_payment_status, report_error |
| `dineout_` | 12 | render_restaurants_dineout, search_restaurants_dineout, get_restaurant_details, get_available_slots, create_cart, book_table, confirm_order, get_booking_status, get_saved_locations, get_payment_options, check_payment_status, report_error |

Run `tools/list` against the server for the authoritative live set.

## Safety Gates

- **Reads always work**: search, menus, product lookup, slots, addresses, order history.
- **12 mutating tools are off by default**: cart updates/clears (all streams),
  coupon apply, cart creation, table booking. Server must run with
  `SWIGGY_LYR_ALLOW_ORDERS=1`.
- **High-risk tools additionally require `confirm=true`**:
  `*_confirm_order`, `*_checkout`, `place_food_order`, `book_table` —
  COD orders cannot be cancelled. Always show the user the full cart before confirming.
- Keep the Swiggy app closed during agent use (session conflicts).

## Authentication

```bash
swiggy-lyr --login            # browser OAuth flow
swiggy-lyr --login --token X  # manual bearer token
swiggy-lyr --status           # TOON status: authenticated / expiry
```

Or set `SWIGGY_LYR_TOKEN` env var.
