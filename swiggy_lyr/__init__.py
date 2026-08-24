"""swiggy-lyr — unified Swiggy MCP (Food + Instamart + Dineout) for AI agents."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("swiggy-lyr")
except PackageNotFoundError:  # running from a source tree without install
    __version__ = "0.0.0"
