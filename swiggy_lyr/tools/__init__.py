"""Tool registrars — one module per upstream stream.

`tools` accepts a pre-discovered inventory (parallel startup path); None
falls back to inline discovery for that single stream.
"""

from fastmcp import FastMCP

from swiggy_lyr.upstream.proxy import register_stream_tools
from swiggy_lyr.upstream.streams import STREAMS


def register_food_tools(mcp: FastMCP, tools: list | None = None) -> int:
    return register_stream_tools(mcp, "food", STREAMS["food"], tools=tools)


def register_instamart_tools(mcp: FastMCP, tools: list | None = None) -> int:
    return register_stream_tools(mcp, "instamart", STREAMS["instamart"], tools=tools)


def register_dineout_tools(mcp: FastMCP, tools: list | None = None) -> int:
    return register_stream_tools(mcp, "dineout", STREAMS["dineout"], tools=tools)
