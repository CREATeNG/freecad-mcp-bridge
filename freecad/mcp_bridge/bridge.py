"""MCP Bridge lifecycle facade.

Owns the in-process HTTP server singleton and exposes start/stop/is_running
to the toolbar toggle.
"""

from freecad.mcp_bridge.http_server import HttpServer

_server = HttpServer()


def start(
    addresses: list[str] | None = None, port: int | None = None
) -> None:
    """Start the HTTP MCP server (idempotent)."""
    _server.start(addresses=addresses, port=port)


def stop() -> None:
    """Stop the HTTP server if running (idempotent)."""
    _server.stop()


def is_running() -> bool:
    """True while the server thread is alive."""
    return _server.is_running()
