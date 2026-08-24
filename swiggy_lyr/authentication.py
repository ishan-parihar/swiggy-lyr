"""Session validation for the CLI status view and tool guards."""

import os
import time

from swiggy_lyr.session_state import ENV_VAR, get_bearer_token, load_token


def validate_session() -> bool:
    try:
        get_bearer_token()
        return True
    except Exception:
        return False


def get_auth_status() -> dict:
    """Auth state for `--status`. The env token wins when present: it is what
    every upstream call would actually use, so file-derived fields must not
    masquerade as its properties."""
    payload = load_token()

    if (os.environ.get(ENV_VAR) or "").strip():
        return {
            "authenticated": True,
            "mode": "env",
            "source": "env",
            "expires_in_days": None,  # env tokens carry no known expiry
        }

    authenticated = False
    try:
        get_bearer_token()
        authenticated = True
    except Exception:
        pass

    mode = None
    expires_in_days = None
    if payload:
        stored_mode = payload.get("mode")
        mode = str(stored_mode) if stored_mode else None
        exp = payload.get("expires_at")
        if isinstance(exp, (int, float)):
            expires_in_days = round(max(0.0, (exp - time.time()) / 86400), 1)

    return {
        "authenticated": authenticated,
        "mode": mode,
        "source": "file" if authenticated else None,
        "expires_in_days": expires_in_days,
    }
