"""The three upstream Swiggy MCP streams."""

from collections.abc import Awaitable, Callable

FOOD_URL = "https://mcp.swiggy.com/food"
INSTAMART_URL = "https://mcp.swiggy.com/im"
DINEOUT_URL = "https://mcp.swiggy.com/dineout"

STREAMS: dict[str, str] = {
    "food": FOOD_URL,
    "instamart": INSTAMART_URL,
    "dineout": DINEOUT_URL,
}

# Local tool names are prefixed per stream (family convention: <platform>_<action>).
TOOL_PREFIX: dict[str, str] = {
    "food": "food_",
    "instamart": "instamart_",
    "dineout": "dineout_",
}

# type alias for the injected list/call functions (tests swap these for fakes)
Lister = Callable[[str], Awaitable[list]]
Caller = Callable[[str, str, dict], Awaitable[dict]]
