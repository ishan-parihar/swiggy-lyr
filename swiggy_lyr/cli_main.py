"""swiggy-lyr CLI — AXI-compliant entry point.

Bare invocation starts the MCP server (stdio) so agent configs can use
{"command": "swiggy-lyr"} directly. Errors go to stdout in TOON, exit code 2.
"""

import argparse
import asyncio
import sys

from swiggy_lyr import __version__
from swiggy_lyr.exceptions import SwiggyLyrError

HOME_VIEW = """\
swiggy-lyr — unified Swiggy MCP (Food + Instamart + Dineout) for AI agents

quick start:
  swiggy-lyr --login          OAuth 2.1 + PKCE consent via browser (~5 day token)
  swiggy-lyr --login --token BEARER
                              store a token manually instead of the browser flow
  swiggy-lyr                  run MCP server on stdio  ← point your agent here
  swiggy-lyr --status         auth state, expiry countdown, token path

mcp config:
  {"mcpServers": {"swiggy": {"command": "swiggy-lyr"}}}

safety:
  cart/booking/order tools require SWIGGY_LYR_ALLOW_ORDERS=1;
  checkout/place_order additionally require confirm=true.
  COD orders cannot be cancelled — review carts before confirming.

env:
  SWIGGY_LYR_TOKEN        bypass stored token (CI / manual)
  SWIGGY_LYR_CLIENT_ID    pre-registered OAuth client if DCR is unavailable
  SWIGGY_LYR_ALLOW_ORDERS enable mutating tools (default off)
  SWIGGY_REDIRECT_URI     override callback URI (default http://localhost:9876/callback)
"""


def _kv(key: str, value) -> str:
    if isinstance(value, bool):
        return f"{key}: {'true' if value else 'false'}"
    if isinstance(value, int | float):
        return f"{key}: {value}"
    return f'{key}: "{value}"' if value else f"{key}:"


def axi_error(msg: str, hint: str | None = None, code: int = 2):
    print(_kv("error", msg))
    if hint:
        print(_kv("help", hint))
    sys.exit(code)


def cmd_status() -> None:
    from swiggy_lyr.authentication import get_auth_status
    from swiggy_lyr.session_state import TOKEN_PATH

    status = get_auth_status()
    for key in ("authenticated", "mode", "source", "expires_in_days"):
        if status.get(key) is not None:
            print(_kv(key, status[key]))
    print(f'token_path: "{TOKEN_PATH}"')
    if not status["authenticated"]:
        print(_kv("help", "Run swiggy-lyr --login"))


def cmd_login(args: argparse.Namespace) -> None:
    from swiggy_lyr.oauth import run_login_flow
    from swiggy_lyr.session_state import store_manual_token

    if args.token:
        path = store_manual_token(args.token)
        print(f'saved: "{path}"')
        print('mode: "manual"')
        print(_kv("help", "Expiry unknown until first 401 — refresh with --login when needed"))
        return
    asyncio.run(run_login_flow())


def cmd_serve(args: argparse.Namespace) -> None:
    from swiggy_lyr.server import run

    try:
        run(transport=args.transport, host=args.host, port=args.port)
    except SwiggyLyrError as e:
        axi_error(e.message, e.hint)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="swiggy-lyr",
        description="Unified Swiggy MCP server — Food, Instamart & Dineout",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=HOME_VIEW,
    )
    parser.add_argument("--login", action="store_true", help="run the OAuth consent flow")
    parser.add_argument(
        "--token", metavar="BEARER", help="store this token instead of browser flow"
    )
    parser.add_argument("--status", action="store_true", help="print auth status (TOON)")
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    try:
        if args.login:
            cmd_login(args)
        elif args.status:
            cmd_status()
        else:
            cmd_serve(args)
    except SwiggyLyrError as e:
        axi_error(e.message, e.hint)


if __name__ == "__main__":
    main()
