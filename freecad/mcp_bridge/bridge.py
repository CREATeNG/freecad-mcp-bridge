"""MCP Bridge lifecycle facade.

Owns the in-process HTTP server singleton and exposes start/stop/is_running
to the toolbar toggle.
"""

from freecad.mcp_bridge.http_server import HttpServer

_server = HttpServer()


def start(
    addresses: list[str] | None = None,
    tls_cert_file: str | None = None,
    tls_key_file: str | None = None,
) -> None:
    """Start the HTTP MCP server on `addresses` (idempotent)."""
    _server.start(
        addresses=addresses,
        tls_cert_file=tls_cert_file,
        tls_key_file=tls_key_file,
    )


def stop() -> None:
    """Stop the HTTP server if running (idempotent)."""
    _server.stop()


def is_running() -> bool:
    """True while the server thread is alive."""
    return _server.is_running()


def listening_entries() -> list[str] | None:
    """Entries the bridge is listening on, or None when it is off."""
    return _server.listening_entries()
