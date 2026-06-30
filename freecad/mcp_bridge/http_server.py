"""In-process HTTP server hosting the MCP endpoint on loopback.

A ThreadingHTTPServer runs on a background thread, binding 127.0.0.1 only.
This slice answers the MCP lifecycle (`initialize`, `tools/list`) with plain
JSON. Tool execution (SSE, Qt main-thread dispatch, paging) is added later.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import FreeCAD

from freecad.mcp_bridge import config, mcp_protocol
from freecad.mcp_bridge.constants import ENDPOINT_PATH, LOG_PREFIX

_LOOPBACK_HOSTS = ("localhost", "127.0.0.1")
PARSE_ERROR = -32700


class McpRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Silence the default stderr access log; FreeCAD console is our log.
        pass

    def _origin_ok(self) -> bool:
        # DNS-rebinding protection: reject a present Origin that isn't loopback.
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        return urlparse(origin).hostname in _LOOPBACK_HOSTS

    def do_GET(self):
        # Server-initiated push is out of scope.
        self.send_error(405, "Method Not Allowed")

    def do_POST(self):
        if not self._origin_ok():
            self.send_error(403, "Forbidden Origin")
            return
        if self.path != ENDPOINT_PATH:
            self.send_error(404, "Not Found")
            return

        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        try:
            request = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._send_json(400, _parse_error())
            return

        response = mcp_protocol.handle_request(request)
        if response is None:
            # Notification — acknowledge with no body.
            self.send_response(202)
            self.end_headers()
            return
        self._send_json(200, response)

    def _send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _parse_error():
    return {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": PARSE_ERROR, "message": "Parse error"},
    }


class HttpServer:
    """Owns the ThreadingHTTPServer and its background serving thread."""

    def __init__(self):
        self._httpd = None
        self._thread = None

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_running():
            return
        bind_port = config.port()
        try:
            self._httpd = ThreadingHTTPServer(
                ("127.0.0.1", bind_port), McpRequestHandler
            )
        except OSError as exc:
            self._httpd = None
            FreeCAD.Console.PrintError(
                f"{LOG_PREFIX} Could not bind 127.0.0.1:{bind_port} ({exc}). "
                "Change the port in Edit → Preferences → MCP Bridge.\n"
            )
            raise
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name="mcp-bridge-http",
            daemon=True,
        )
        self._thread.start()
        FreeCAD.Console.PrintMessage(
            f"{LOG_PREFIX} HTTP server listening on "
            f"127.0.0.1:{bind_port}{ENDPOINT_PATH}\n"
        )

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        self._thread = None
        FreeCAD.Console.PrintMessage(f"{LOG_PREFIX} HTTP server stopped\n")
