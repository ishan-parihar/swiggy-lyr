"""Token persistence at ~/.swiggy-lyr/token.json (chmod 600).

Resolution order for the Bearer token:
1. SWIGGY_LYR_TOKEN env var (manual / CI usage)
2. ~/.swiggy-lyr/token.json written by `swiggy-lyr --login`
"""

import json
import os
import time
from pathlib import Path

from swiggy_lyr.exceptions import (
    NotAuthenticatedError,
    SessionStateError,
    TokenExpiredError,
)

TOKEN_DIR = Path.home() / ".swiggy-lyr"
TOKEN_PATH = TOKEN_DIR / "token.json"

ENV_VAR = "SWIGGY_LYR_TOKEN"


def save_token(payload: dict) -> Path:
    """Persist token payload. Adds mode=oauth unless the caller set it."""
    payload.setdefault("mode", "oauth")
    payload.setdefault("saved_at", int(time.time()))
    try:
        TOKEN_DIR.mkdir(parents=True, exist_ok=True)
        TOKEN_PATH.write_text(json.dumps(payload, indent=2))
        TOKEN_PATH.chmod(0o600)
    except OSError as e:
        raise SessionStateError(
            f"Cannot write {TOKEN_PATH}: {e}", hint="Check ~/.swiggy-lyr permissions"
        ) from e
    return TOKEN_PATH


def store_manual_token(token: str) -> Path:
    """Store a Bearer token pasted by hand — expiry unknown until 401."""
    return save_token(
        {
            "access_token": token.strip(),
            "token_type": "Bearer",
            "mode": "manual",
            "expires_at": None,
        }
    )


def load_token() -> dict | None:
    if not TOKEN_PATH.exists():
        return None
    try:
        return json.loads(TOKEN_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def clear_token() -> bool:
    if TOKEN_PATH.exists():
        TOKEN_PATH.unlink()
        return True
    return False


def get_bearer_token() -> tuple[str, str]:
    """Return (token, source). Raises when missing/expired.

    source is "env" or "file" so status output can say where auth came from.
    """
    env_token = (os.environ.get(ENV_VAR) or "").strip()
    if env_token:
        return env_token, "env"

    payload = load_token()
    if not payload or not payload.get("access_token"):
        raise NotAuthenticatedError(
            "No Swiggy credentials found",
            hint="Run `swiggy-lyr --login`, or set SWIGGY_LYR_TOKEN",
        )

    expires_at = payload.get("expires_at")
    if isinstance(expires_at, (int, float)) and time.time() >= expires_at:
        raise TokenExpiredError(
            "Swiggy OAuth token expired (~5 day lifetime)",
            hint="Re-authenticate: swiggy-lyr --login",
        )
    return payload["access_token"], "file"
