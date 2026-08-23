"""Fake upstream for tests — no network."""

from dataclasses import dataclass, field


@dataclass
class FakeTool:
    name: str
    description: str = ""
    inputSchema: dict = field(default_factory=lambda: {"type": "object", "properties": {}})


FAKE_TOOLS = [
    FakeTool(
        "search_restaurants",
        "Search restaurants by cuisine or dish",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["query"],
        },
    ),
    FakeTool(
        "get_menu",
        "Get restaurant menu",
        {
            "type": "object",
            "properties": {"restaurant_id": {"type": "string"}},
            "required": ["restaurant_id"],
        },
    ),
    FakeTool(
        "add_to_cart",
        "Add item to cart",
        {
            "type": "object",
            "properties": {"item_id": {"type": "string"}, "qty": {"type": "integer"}},
            "required": ["item_id"],
        },
    ),
    FakeTool(
        "checkout_cart",
        "Place the order (COD only)",
        {
            "type": "object",
            "properties": {"address_id": {"type": "string"}},
            "required": ["address_id"],
        },
    ),
]


async def fake_lister(url: str) -> list:
    return FAKE_TOOLS


CALLS: list[tuple[str, str, dict]] = []


async def fake_caller(url: str, tool_name: str, arguments: dict) -> dict:
    CALLS.append((url, tool_name, arguments))
    return {"data": {"echo_tool": tool_name, "echo_args": arguments}}
