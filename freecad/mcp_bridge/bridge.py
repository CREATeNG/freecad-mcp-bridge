"""MCP Bridge lifecycle facade.

Owns the in-process HTTP server singleton and exposes start/stop/is_running
to the toolbar toggle. Replaces the former QLocalServer socket transport.
"""

from freecad.mcp_bridge.http_server import HttpServer

_server = HttpServer()


def start() -> None:
    """Start the loopback HTTP MCP server (idempotent)."""
    _server.start()


def stop() -> None:
    """Stop the HTTP server if running (idempotent)."""
    _server.stop()


def is_running() -> bool:
    """True while the server thread is alive."""
    return _server.is_running()
