class SwiggyLyrError(RuntimeError):
    """Base error for swiggy-lyr. Carries an optional agent-facing hint."""

    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.hint = hint


class OAuthError(SwiggyLyrError):
    """OAuth discovery, registration, or token exchange failed."""


class NotAuthenticatedError(SwiggyLyrError):
    """No token stored and no SWIGGY_LYR_TOKEN env var."""


class TokenExpiredError(SwiggyLyrError):
    """Upstream returned 401 — the 5-day Bearer token has lapsed."""


class UpstreamError(SwiggyLyrError):
    """An upstream Swiggy MCP stream failed or is unreachable."""


class OrderSafetyError(SwiggyLyrError):
    """A mutating tool was invoked without SWIGGY_LYR_ALLOW_ORDERS=1 / confirm=true."""


class SessionStateError(SwiggyLyrError):
    """Token store is corrupt or unwritable."""
