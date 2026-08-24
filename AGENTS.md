# AGENTS.md — swiggy-lyr

## What this is

A unified Swiggy MCP server that proxies Swiggy's three official remote MCP
streams (food, instamart, dineout) into one local stdio server. Unlike the
other `-lyr` projects, there is no scraping: auth is OAuth 2.1 + PKCE and the
HTTP core is an MCP client.

## Development Commands

```bash
uv sync                      # install deps
uv run pytest -q             # tests (no live network — fakes only)
uv run ruff check .          # lint
uv run ruff format .         # format
uv run swiggy-lyr --status   # CLI status (TOON)
uv run swiggy-lyr            # start server on stdio
```

## Architecture

```
swiggy_lyr/
├── cli_main.py        AXI CLI: bare=serve, --login, --status; TOON errors exit 2
├── server.py          create_mcp_server() → FastMCP("swiggy-lyr")
├── oauth.py           RFC 9728 discovery → DCR → PKCE → localhost callback
├── session_state.py   token store ~/.swiggy-lyr/token.json (chmod 600)
├── authentication.py  validate_session(), get_auth_status()
├── upstream/
│   ├── streams.py     stream URLs + tool prefixes
│   ├── client.py      mcp SDK streamable-http client, Bearer injection
│   └── proxy.py       dynamic tool generation + safety gate
└── tools/__init__.py  per-stream registrar functions
```

## Conventions

- Tool names are `<stream>_<upstream_name>`: `food_search_restaurants`, etc.
- Read-only tools get `readOnlyHint=True`; mutating tools don't.
- Mutating tools require `SWIGGY_LYR_ALLOW_ORDERS=1`; checkout/place_order
  additionally require `confirm=true`. Do not weaken this gate.
- Tests inject `lister=`/`caller=` fakes — never hit live Swiggy in CI.
- Errors raise SwiggyLyrError subclasses carrying a `hint`; cli_main maps to
  TOON stdout + exit 2 (untyped exceptions included). `--status` exits 2 when
  unauthenticated. Upstream 401s become TokenExpiredError with a re-login
  hint — the mcp SDK wraps failures in ExceptionGroups, so error translation
  must walk `e.exceptions`, never just `str(e)`.
- Upstream tool registration is per-tool fault-isolated: one unproxiable tool
  is skipped with an ERROR log; it never takes down its stream or server.

## Release Process

Two channels:

**Source installs (default today):** install.sh and `uv tool install git+…` always
build from `main` — pushing to main is the release for git users. Before pushing:

```bash
uv version --bump patch && uv lock
uv run ruff check . && uv run pytest -q
```

**PyPI (tag-gated):** pushing `v*` runs CI, then trusted-publishes to PyPI
(`.github/workflows/publish.yml`). One-time setup: add the repo as a trusted
publisher on PyPI (owner/ishan-parihar, repo/swiggy-lyr, workflow publish.yml,
environment pypi). Until then the publish job fails harmlessly; git installs
are unaffected.

```bash
git tag v0.1.1 && git push origin v0.1.1
```

## Commit Messages

Conventional commits: `type(scope): subject` (<50 chars, imperative).
