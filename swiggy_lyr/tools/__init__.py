"""Tool registrars — one module per upstream stream."""

from fastmcp import FastMCP

from swiggy_lyr.upstream.proxy import register_stream_tools
from swiggy_lyr.upstream.streams import STREAMS


def register_food_tools(mcp: FastMCP) -> int:
    return register_stream_tools(mcp, "food", STREAMS["food"])


def register_instamart_tools(mcp: FastMCP) -> int:
    return register_stream_tools(mcp, "instamart", STREAMS["instamart"])


def register_dineout_tools(mcp: FastMCP) -> int:
    return register_stream_tools(mcp, "dineout", STREAMS["dineout"])
