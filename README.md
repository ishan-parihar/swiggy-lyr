<!-- T2I HERO SPEC — Subject: three Swiggy streams (food bowl orange, grocery bag green, dinner table purple) converging through a pipe into one MCP server node feeding JSON to an agent. Composition: 3 streams → merge valve → single node → agent. Palette: Swiggy orange #fc8019, instamart green, dineout purple → dark slate. Style: flat vector, no text, 16:9. -->

<p align="center">
<img src="https://img.shields.io/badge/python-3.11+-2b6cb0?style=flat&logo=python&logoColor=white" alt="Python 3.11+">
<img src="https://img.shields.io/badge/mcp-1.0-c026d3?style=flat&logo=modelcontextprotocol&logoColor=white" alt="MCP 1.0">
<img src="https://img.shields.io/badge/streams-3-fc8019?style=flat" alt="3 streams">
<img src="https://img.shields.io/badge/license-MIT-059669?style=flat" alt="MIT">
</p>

<h1 align="center">swiggy-lyr</h1>

<p align="center">
<strong>One Swiggy MCP for AI agents</strong> — Food delivery, Instamart groceries, and
Dineout table bookings unified behind a local stdio server with a single OAuth token.
</p>

---

## What it is

Swiggy runs three official remote MCP servers (`mcp.swiggy.com/{food,im,dineout}`), each with its own connection. **swiggy-lyr** aggregates all ~35 upstream tools into one server your agent connects to once:

| Stream | Prefix | Covers |
|---|---|---|
| **Food** | `food_` | restaurant search, menus, cart, ordering, tracking |
| **Instamart** | `instamart_` | product search, cart, checkout |
| **Dineout** | `dineout_` | discovery, details, slots, free bookings |

Upstream tool schemas are discovered dynamically at startup — Swiggy schema changes propagate without code changes here.

## Quick start

```bash
# Install
curl -sSL https://raw.githubusercontent.com/ishan-parihar/swiggy-lyr/main/install.sh | bash

# Authenticate (browser OAuth consent, ~5 day token)
swiggy-lyr --login

# Check status
swiggy-lyr --status

# Run the MCP server (stdio)
swiggy-lyr
```

### Agent config

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

No token yet? `SWIGGY_LYR_TOKEN=<bearer>` env var works too.

## Order safety

Swiggy's COD orders **cannot be cancelled**, so swiggy-lyr ships gated by default:

1. Cart mutations / bookings require env `SWIGGY_LYR_ALLOW_ORDERS=1` on the server process.
2. Checkout / place-order tools additionally require the agent to pass `confirm=true`.

Reads (search, menus, slots, cart view) always work.

## Architecture

```
┌────────────────────── swiggy-lyr (stdio FastMCP) ──────────────────────┐
│  food_*        instamart_*        dineout_*      ← generated proxies   │
│        └──────────────┴───────────────────┘                            │
│              dynamic tools/list → local registration                   │
│                        Bearer (OAuth 2.1 + PKCE)                       │
└────────────────────────────────┬───────────────────────────────────────┘
                                 ▼
     mcp.swiggy.com/food    mcp.swiggy.com/im    mcp.swiggy.com/dineout
```

## CLI

```
swiggy-lyr --login            OAuth consent via browser
swiggy-lyr --login --token X  store a bearer token manually
swiggy-lyr --status           auth state + expiry countdown (TOON)
swiggy-lyr                    start MCP server on stdio
swiggy-lyr --transport http --port 8000
```

## Environment

| Var | Purpose |
|---|---|
| `SWIGGY_LYR_TOKEN` | bypass stored token (CI / manual) |
| `SWIGGY_LYR_ALLOW_ORDERS` | enable mutating tools (default off) |
| `SWIGGY_LYR_CLIENT_ID` | pre-registered client if DCR unavailable |
| `SWIGGY_REDIRECT_URI` | override callback URI |
| `LOG_LEVEL` | DEBUG/INFO/WARNING |

## Notes

- Keep the Swiggy app closed while agents use this (session conflicts).
- Third-party development is in Swiggy's security-review window; treat this as personal-use tooling.
- Development: see [AGENTS.md](AGENTS.md). Install from source with `uv sync`.

## License

MIT
