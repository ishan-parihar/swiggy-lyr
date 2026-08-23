"""Regression: the favicon-after-callback race that broke live OAuth.

Browser hits /callback?code=...&state=... then immediately /favicon.ico.
The second, empty-query request must not clobber the captured params.
"""

import threading
import time
import urllib.error
import urllib.request

import pytest

from swiggy_lyr import oauth
from swiggy_lyr.oauth import wait_for_callback


def _free_port() -> int:
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _get(port: int, path: str) -> int:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def _wait_ready(port: int, path: str = "/favicon.ico", expect: int = 404) -> None:
    """Block until the callback server answers; avoids bind/serve races."""
    for _ in range(60):
        try:
            if _get(port, path) == expect:
                return
        except OSError:
            pass
        time.sleep(0.05)
    raise AssertionError(f"callback server never became ready on {port}")


def test_favicon_after_code_does_not_clobber():
    port = _free_port()
    result: dict = {}

    def waiter():
        result["query"] = wait_for_callback(port, timeout=10)

    t = threading.Thread(target=waiter, daemon=True)
    t.start()
    _wait_ready(port)  # noise request BEFORE the code (some browsers preflight)

    # 2) the real OAuth redirect — huge code like Swiggy's real ~2KB one
    big_code = "A" * 2048
    assert _get(port, f"/callback?code={big_code}&state=s3cret") == 200
    t.join(timeout=10)
    assert not t.is_alive()

    # 3) trailing favicon AFTER completion: server may already be closed —
    # browsers swallow this either way; the point is the captured params survive.
    try:
        _get(port, "/favicon.ico")
    except (urllib.error.URLError, ConnectionError, OSError):
        pass

    q = result["query"]
    assert q["code"] == [big_code]
    assert q["state"] == ["s3cret"]


def test_error_response_terminates_wait():
    port = _free_port()
    result: dict = {}

    def waiter():
        try:
            result["query"] = wait_for_callback(port, timeout=10)
        except Exception as e:  # noqa: BLE001 - test boundary
            result["error"] = str(e)

    t = threading.Thread(target=waiter, daemon=True)
    t.start()
    time.sleep(0.2)
    assert _get(port, "/callback?error=access_denied&state=x") == 400
    t.join(timeout=10)
    assert not t.is_alive()
    assert result["query"]["error"] == ["access_denied"]
    # state machine reset for next run
    assert oauth._CallbackState.query is None


@pytest.mark.parametrize("path", ["/", "/favicon.ico"])
def test_noise_before_code_is_ignored_then_timeout(path):
    """Empty-query requests never satisfy the wait (query-carrying ones do)."""
    port = _free_port()

    def waiter():
        try:
            wait_for_callback(port, timeout=2)
            result["ok"] = True  # type: ignore[possibly-undefined]
        except oauth.OAuthError as e:
            result["err"] = str(e)  # type: ignore[possibly-undefined]

    result: dict = {}
    t = threading.Thread(target=waiter, daemon=True)
    t.start()
    _wait_ready(port)
    _get(port, path)  # ignored
    t.join(timeout=10)
    assert "Timed out" in result.get("err", "")
