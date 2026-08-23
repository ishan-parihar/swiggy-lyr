"""Session validation for the CLI status view and tool guards."""

import os
import time

from swiggy_lyr.session_state import get_bearer_token, load_token


def validate_session() -> bool:
    try:
        get_bearer_token()
        return True
    except Exception:
        return False


def get_auth_status() -> dict:
    env_token = os.environ.get("SWIGGY_LYR_TOKEN")
    payload = load_token()

    authenticated = False
    source = None
    mode = None
    expires_in_days = None

    try:
        _, src = get_bearer_token()
        authenticated = True
        source = src
        mode = "env" if env_token else ("oauth" if payload else "env")
    except Exception:
        pass

    if payload:
        stored_mode = payload.get("mode")
        if stored_mode:
            mode = stored_mode
        exp = payload.get("expires_at")
        if isinstance(exp, (int, float)):
            expires_in_days = round(max(0.0, (exp - time.time()) / 86400), 1)

    return {
        "authenticated": authenticated,
        "mode": mode,
        "source": source,
        "expires_in_days": expires_in_days,
    }
