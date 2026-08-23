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

Tools are discovered from the upstream streams at startup and prefixed:

| Prefix | Domain | Typical tools |
|--------|--------|---------------|
| `food_` | Restaurant delivery | search restaurants, browse menus, cart ops, place order, track order |
| `instamart_` | Groceries/quick-commerce | search products, view cart, checkout |
| `dineout_` | Table bookings | discover venues, get details/slots, book table (free) |

Run `tools/list` against the server for the authoritative set (~35 tools).

## Safety Gates

- **Reads always work**: search, menus, product lookup, slot checks, cart view.
- **Mutations are off by default** (`add to cart`, bookings, orders). The server
  must run with `SWIGGY_LYR_ALLOW_ORDERS=1`.
- **Checkout/place-order additionally requires `confirm=true`** — COD orders
  cannot be cancelled. Always show the user the full cart before confirming.
- Keep the Swiggy app closed during agent use (session conflicts).

## Authentication

```bash
swiggy-lyr --login            # browser OAuth flow
swiggy-lyr --login --token X  # manual bearer token
swiggy-lyr --status           # TOON status: authenticated / expiry
```

Or set `SWIGGY_LYR_TOKEN` env var.
