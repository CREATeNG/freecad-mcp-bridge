"""Shared identifiers and defaults for the MCP Bridge addon."""

DISPLAY_NAME = "MCP Bridge"
LOG_PREFIX = "[MCP Bridge]"
COMMAND_ID = "MCP_Bridge_Toggle"
TOOLBAR_OBJECT_NAME = "MCP_Bridge"

# MCP server identity (serverInfo.version is read live from package.xml)
SERVER_NAME = "freecad-mcp-bridge"
PROTOCOL_VERSION = "2025-03-26"

# HTTP endpoint path the MCP server answers on
ENDPOINT_PATH = "/mcp"

# Network / timing defaults — overridable via FreeCAD preferences (see config.py)
DEFAULT_PORT = 39280
DEFAULT_TIMEOUT_MS = 15000

# FreeCAD preference group backing config.py
PREF_GROUP = "User parameter:BaseApp/Preferences/Mod/freecad-mcp-bridge"
