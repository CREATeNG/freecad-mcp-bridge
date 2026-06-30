"""FreeCAD MCP Bridge — in-process MCP server exposed over loopback HTTP.

Intentionally import-light: pulling in the server (and thus FreeCAD) is left
to init_gui at addon load, so pure modules like mcp_protocol stay importable
on plain CPython for testing.
"""
