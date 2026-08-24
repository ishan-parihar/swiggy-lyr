from fastmcp import FastMCP

from swiggy_lyr.exceptions import UpstreamError
from swiggy_lyr.tools import (
    register_dineout_tools,
    register_food_tools,
    register_instamart_tools,
)
from swiggy_lyr.upstream.proxy import _run_discovery, discover_streams
from swiggy_lyr.upstream.streams import STREAMS


def create_mcp_server(discovered: dict[str, list] | None = None) -> FastMCP:
    """Build the server.

    `discovered` injects pre-fetched tool inventories ({stream: tools}); when
    omitted, all three streams are discovered concurrently here. Streams whose
    inventory is empty/missing register zero tools but never block the others.
    """
    mcp = FastMCP(
        "swiggy-lyr",
        instructions=(
            "Unified Swiggy access for AI agents: Food delivery, Instamart groceries, "
            "and Dineout table bookings — one server, one OAuth token.\n"
            "Tool names are prefixed by stream: food_*, instamart_*, dineout_*.\n"
            "Ordering/booking tools are disabled unless SWIGGY_LYR_ALLOW_ORDERS=1; "
            "checkout additionally requires confirm=true (COD orders cannot be cancelled)."
        ),
    )

    if discovered is None:
        discovered = _run_discovery(discover_streams(STREAMS))

    food = register_food_tools(mcp, tools=discovered.get("food", []))
    instamart = register_instamart_tools(mcp, tools=discovered.get("instamart", []))
    dineout = register_dineout_tools(mcp, tools=discovered.get("dineout", []))

    import logging

    logging.getLogger("swiggy_lyr").info(
        "swiggy-lyr ready: food=%d instamart=%d dineout=%d", food, instamart, dineout
    )
    if food + instamart + dineout == 0:
        raise UpstreamError(
            "No Swiggy streams reachable — 0 tools registered",
            hint="Run swiggy-lyr --login first; then swiggy-lyr --status",
        )
    return mcp


def run(transport: str = "stdio", host: str = "127.0.0.1", port: int = 8000) -> None:
    mcp = create_mcp_server()
    if transport == "http":
        mcp.run(transport="http", host=host, port=port)
    else:
        mcp.run()
