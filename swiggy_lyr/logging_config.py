import logging
import os
import sys

_FMT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def _make_logger() -> logging.Logger:
    logger = logging.getLogger("swiggy_lyr")
    if not logger.handlers:
        # stderr only — stdout belongs to the MCP protocol on stdio transport.
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(_FMT))
        logger.addHandler(handler)
    level = logging.getLevelName(os.environ.get("LOG_LEVEL", "INFO").upper())
    logger.setLevel(level if isinstance(level, int) else logging.INFO)
    return logger


logger = _make_logger()
