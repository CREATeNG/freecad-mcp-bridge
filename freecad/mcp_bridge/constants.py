"""Shared identifiers and defaults for the MCP Bridge addon."""

DISPLAY_NAME = "MCP Bridge"
LOG_PREFIX = "[MCP Bridge]"
COMMAND_ID = "MCP_Bridge_Toggle"
TOOLBAR_OBJECT_NAME = "MCP_Bridge"
STATUS_BUTTON_OBJECT_NAME = "MCPBridgeStatusButton"

# MCP server identity (serverInfo.version is read live from package.xml)
SERVER_NAME = "freecad-mcp-bridge"
# The MCP protocol versions the server speaks; it rejects every other.
# 2026-07-28 carries its version on every request; 2025-11-25, the legacy
# version, opens with an initialize handshake.
PROTOCOL_VERSION = "2026-07-28"
LEGACY_PROTOCOL_VERSION = "2025-11-25"
SUPPORTED_PROTOCOL_VERSIONS = (PROTOCOL_VERSION, LEGACY_PROTOCOL_VERSION)

# HTTP endpoint path the MCP server answers on
ENDPOINT_PATH = "/mcp"

# Network / timing defaults — overridable via FreeCAD preferences (see config.py)
DEFAULT_PORT = 39280
DEFAULT_MAX_RESPONSE_TIMEOUT_S = 15
DEFAULT_MAX_PAGE_SIZE_KB = 32
DEFAULT_MAX_HISTORY_RETENTION_MIN = 5

# FreeCAD preference group backing config.py
PREF_GROUP = "User parameter:BaseApp/Preferences/Mod/freecad-mcp-bridge"
