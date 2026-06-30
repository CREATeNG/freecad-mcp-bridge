"""Runtime configuration for the MCP Bridge, read from FreeCAD preferences.

Values live under PREF_GROUP and are read fresh on each access so a
preference change takes effect on the next server start without a restart.
"""

import FreeCAD

from freecad.mcp_bridge.constants import (
    DEFAULT_PORT,
    DEFAULT_TIMEOUT_MS,
    PREF_GROUP,
)


def _params():
    return FreeCAD.ParamGet(PREF_GROUP)


def port() -> int:
    """TCP port for the loopback HTTP server."""
    return _params().GetInt("Port", DEFAULT_PORT)


def response_timeout_ms() -> int:
    """Max time a tool call waits for output before returning has_more=True."""
    return _params().GetInt("ResponseTimeout", DEFAULT_TIMEOUT_MS)
