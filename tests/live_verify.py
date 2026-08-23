"""Live production verification — real token, real streams, no mocks.

Usage: uv run python tests/live_verify.py
Requires ~/.swiggy-lyr/token.json from `swiggy-lyr --login`.
"""

import asyncio
import json
import os
import sys

from fastmcp import Client

from swiggy_lyr.server import create_mcp_server


def section(title):
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")


def _extract_address_id(text: str):
    import re

    try:
        data = json.loads(text)

        def find_id(o):
            if isinstance(o, dict):
                for k in ("id", "addressId", "address_id", "userAddressId"):
                    if k in o and isinstance(o[k], (str, int)):
                        return o[k]
                for v in o.values():
                    r = find_id(v)
                    if r:
                        return r
            elif isinstance(o, list):
                for v in o:
                    r = find_id(v)
                    if r:
                        return r
            return None

        found = find_id(data)
        if found is not None:
            return found
    except (json.JSONDecodeError, ValueError):
        pass
    m = re.search(r'"(?:addressId|id)"\s*:\s*"?(\d+)', text)
    return m.group(1) if m else None


async def main() -> int:
    failures = []

    section("1) Server startup: live discovery on all 3 streams")
    mcp = create_mcp_server()

    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
        by_prefix = {}
        for n in tools:
            p = n.split("_", 1)[0]
            by_prefix.setdefault(p, []).append(n)
        for p in ("food", "instamart", "dineout"):
            names = sorted(by_prefix.get(p, []))
            print(f"{p}: {len(names)} tools")
            for n in names:
                print(f"  - {n}")
        if not tools:
            failures.append("zero tools registered")

        section("2) Read tool: food stream")
        food_reads = [
            n
            for n in by_prefix.get("food", [])
            if tools[n].annotations and getattr(tools[n].annotations, "readOnlyHint", False)
        ]
        print(f"read-only food tools: {len(food_reads)} of {len(by_prefix.get('food', []))}")
        # try search-ish first read tool with minimal args
        for name in food_reads:
            required = (tools[name].inputSchema or {}).get("required", [])
            print(f"  probe {name} required={required}")
            break

        section("2b) Real read chain: get_addresses → search_restaurants")
        try:
            res = await client.call_tool("food_get_addresses", {})
            text = "\n".join(getattr(c, "text", "") for c in (res.content or []))
            if res.is_error:
                failures.append(f"get_addresses errored: {text[:150]}")
            addr_id = _extract_address_id(text)
            print(f"addresses ok; extracted addressId={addr_id}")
            if addr_id is None:
                print("(no saved address on account — skipping search step)")
            else:
                res2 = await client.call_tool(
                    "food_search_restaurants",
                    {"query": "pizza", "addressId": str(addr_id)},
                )
                t2 = "\n".join(getattr(c, "text", "") for c in (res2.content or []))
                print(f"search is_error={res2.is_error} bytes={len(t2)}")
                print(t2[:300])
                if res2.is_error:
                    failures.append(f"search errored: {t2[:150]}")
        except Exception as e:  # noqa: BLE001
            failures.append(f"read chain failed: {str(e)[:200]}")

        section("3) Safety gate: mutating tool WITHOUT env")
        mutating = [
            n
            for n in tools
            if not (tools[n].annotations and getattr(tools[n].annotations, "readOnlyHint", False))
        ]
        print(f"gated tools total: {len(mutating)}")
        for n in sorted(mutating)[:40]:
            print(f"  [gated] {n}")
        assert os.environ.get("SWIGGY_LYR_ALLOW_ORDERS") != "1"
        if mutating:
            target = next(
                (n for n in sorted(mutating) if "checkout" not in n and "order" not in n.lower()),
                sorted(mutating)[0],
            )
            schema = tools[target].inputSchema or {}
            args = {
                k: ("x" if (v.get("type", "string") == "string") else 1)
                for k, v in (schema.get("properties") or {}).items()
                if k in set(schema.get("required") or [])
            }
            print(f"attempting gated call: {target} args={args}")
            from fastmcp.exceptions import ToolError

            try:
                await client.call_tool(target, args)
                failures.append(f"gate did NOT block {target}")
            except ToolError as e:
                msg = str(e)
                print(f"gate blocked as expected: {msg[:140]}")
                if "SWIGGY_LYR_ALLOW_ORDERS" not in msg:
                    failures.append(f"gate error lacks hint: {msg[:120]}")
                    return 1

    section("RESULT")
    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    print("ALL LIVE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
