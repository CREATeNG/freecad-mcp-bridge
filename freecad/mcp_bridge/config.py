"""Runtime configuration for the MCP Bridge, read from FreeCAD preferences.

Values live under PREF_GROUP and are read fresh on each access so a
preference change takes effect on the next server start without a restart.
"""

import FreeCAD

from freecad.mcp_bridge.constants import (
    DEFAULT_MAX_PAGE_SIZE_KB,
    DEFAULT_MAX_RESPONSE_TIMEOUT_S,
    DEFAULT_PORT,
    PREF_GROUP,
)


def _params():
    return FreeCAD.ParamGet(PREF_GROUP)


def port() -> int:
    """TCP port for the loopback HTTP server."""
    return _params().GetInt("Port", DEFAULT_PORT)


def max_response_timeout_ms() -> int:
    """Max time a tool call waits for output before returning has_more=True."""
    seconds = _params().GetInt("MaxResponseTimeoutSeconds", DEFAULT_MAX_RESPONSE_TIMEOUT_S)
    return seconds * 1000


def max_page_size_chars() -> int:
    """Max size of a single page's output before returning has_more=True."""
    kb = _params().GetInt("MaxPageSize", DEFAULT_MAX_PAGE_SIZE_KB)
    return kb * 1024
