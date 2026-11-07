"""In-process HTTP server hosting the MCP endpoint.

A ThreadingHTTPServer runs on a background thread for each configured listen
address (127.0.0.1:39280 by default). Each request is served in MCP 2026-07-28
or 2025-11-25, whichever it is in, or rejected (see mcp_protocol.py), and
answered as plain JSON. Tool calls (execute_python, execute_python_file,
get_output_page) run code on the Qt main thread via the Executor. Output is
paged through the job's page history (see paging.py) when it outlasts the
response timeout or exceeds the max page size. A 2026-07-28
subscriptions/listen alone answers as an SSE stream, which ends as soon as it
is acknowledged.
"""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse

import FreeCAD

from freecad.mcp_bridge import config, mcp_protocol, paging
from freecad.mcp_bridge.constants import (
    ENDPOINT_PATH,
    LOG_PREFIX,
    PROTOCOL_VERSION,
)
from freecad.mcp_bridge.executor import Executor

_LOOPBACK_HOSTS = ("localhost", "127.0.0.1")
PARSE_ERROR = -32700
TLS_HANDSHAKE_TIMEOUT_S = 5.0


class McpRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Silence the default stderr access log; FreeCAD console is our log.
        pass

    def handle(self):
        if hasattr(self.request, "do_handshake"):
            self.request.settimeout(TLS_HANDSHAKE_TIMEOUT_S)
            try:
                self.request.do_handshake()
            except OSError:
                return
            self.request.settimeout(None)
        super().handle()

    def _origin_ok(self) -> bool:
        # DNS-rebinding protection: reject a present Origin that isn't loopback.
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        return urlparse(origin).hostname in _LOOPBACK_HOSTS

    def _token_ok(self) -> bool:
        if not getattr(self.server, "require_token", False):
            return True
        auth = self.headers.get("Authorization", "")
        scheme, sep, token = auth.partition(" ")
        if sep != " " or scheme.lower() != "bearer":
            return False
        return config.verify_token(token.strip())

    def _send_unauthorized(self):
        body = b"Unauthorized\n"
        self.send_response(401, "Unauthorized")
        self.send_header("WWW-Authenticate", "Bearer")
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        # The server offers no GET stream, and has no sessions to DELETE.
        self.send_error(405, "Method Not Allowed")

    do_DELETE = do_GET

    def do_POST(self):
        if not self._origin_ok():
            self.send_error(403, "Forbidden Origin")
            return
        if not self._token_ok():
            self._send_unauthorized()
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

        if not isinstance(request, dict):
            self._send_json(400, _invalid_request())
            return

        if mcp_protocol.is_notification(request):
            # JSON-RPC never replies to a notification. MCP defines no
            # notification that asks for work, so nothing runs either.
            self.send_response(202)
            self.end_headers()
            return

        version, rejection = mcp_protocol.check_request(
            request, self.headers
        )
        if rejection is not None:
            self._send_json(*rejection)
            return

        method = request.get("method")
        if method == "tools/call":
            self._handle_tool_call(request, version)
        elif (
            method == "subscriptions/listen"
            and version == PROTOCOL_VERSION
        ):
            self._send_sse(mcp_protocol.listen_messages(request))
        else:
            self._send_json(*mcp_protocol.handle_request(request, version))

    def _handle_tool_call(self, request, version):
        req_id = request.get("id")
        params = request.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        drain_args = (
            config.max_response_timeout_ms(),
            config.max_page_size_chars(),
        )

        if name == "execute_python":
            self._run_code(req_id, version, args.get("code", ""), drain_args)
        elif name == "execute_python_file":
            filepath = args.get("filepath", "")
            try:
                with open(filepath, "r", encoding="utf-8") as handle:
                    code = handle.read()
            except (OSError, UnicodeDecodeError) as exc:
                self._send_tool_result(
                    req_id,
                    version,
                    {
                        "has_more": False,
                        "error": f"Error reading file '{filepath}': {exc}",
                        "page": [],
                    },
                )
                return
            self._run_code(
                req_id, version, code, drain_args, os.path.abspath(filepath)
            )
        elif name == "get_output_page":
            page_no = _as_page_no(args.get("page_no"))
            if page_no is None:
                result = {
                    "has_more": False,
                    "error": "page_no out of range",
                    "page": [],
                }
            else:
                result = paging.fetch(
                    args.get("job_token", ""), page_no, *drain_args
                )
            self._send_tool_result(req_id, version, result)
        else:
            self._send_json(
                200,
                mcp_protocol.error_response(
                    req_id,
                    mcp_protocol.INVALID_PARAMS,
                    f"Unknown tool: {name}",
                ),
            )

    def _run_code(self, req_id, version, code, drain_args, filepath=None):
        token, output_queue = paging.start_page()
        self.server.executor.submit(
            code, output_queue, filepath, token=token
        )
        # The initiating call returns page 0 — the first page the job produces.
        result = paging.fetch(token, 0, *drain_args)
        self._send_tool_result(req_id, version, result)

    def _send_tool_result(self, req_id, version, result):
        self._send_json(
            200, mcp_protocol.tool_call_response(req_id, result, version)
        )

    def _send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_sse(self, messages):
        """Stream the messages as SSE events, one each, then close."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()
        for message in messages:
            body = json.dumps(message).encode("utf-8")
            self.wfile.write(b"event: message\ndata: " + body + b"\n\n")


def _as_page_no(value):
    """Coerce a page_no argument to a non-negative int, or None if invalid."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _parse_error():
    return {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": PARSE_ERROR, "message": "Parse error"},
    }


def _invalid_request():
    return mcp_protocol.error_response(
        None, mcp_protocol.INVALID_REQUEST, "Invalid request"
    )


def _non_loopback_warning(
    entries: list[str],
    non_loopback: list[str],
    require_token: bool,
    require_tls: bool,
) -> str:
    endpoints = ", ".join(entries)
    non_loopback_endpoints = ", ".join(non_loopback)
    who = (
        "Any client with the bearer token"
        if require_token
        else "Any machine"
    )
    if require_token and require_tls:
        return (
            f"{LOG_PREFIX} WARNING: listening on {endpoints}. Bearer "
            f"token and TLS are required beyond loopback. {who} that can "
            "reach these addresses can run Python code in FreeCAD as your "
            f"user account: {non_loopback_endpoints}.\n"
        )
    if not require_token and not require_tls:
        missing = "no authentication or encryption"
    elif not require_token:
        missing = "no authentication"
    else:
        missing = "no encryption"
    return (
        f"{LOG_PREFIX} WARNING: listening on {endpoints} with {missing}. "
        f"{who} that can reach these addresses can run Python code in "
        f"FreeCAD as your user account: {non_loopback_endpoints}.\n"
    )


class HttpServer:
    """Owns the HTTP servers, their serving threads, and the Executor."""

    def __init__(self):
        self._servers = []
        self._executor = None

    def is_running(self) -> bool:
        return bool(self._servers) and all(
            thread.is_alive() for _, thread in self._servers
        )

    def _cleanup_held(self) -> None:
        for srv, thread in reversed(self._servers):
            if thread.is_alive():
                srv.shutdown()
            srv.server_close()
        self._servers = []
        if self._executor is not None:
            self._executor.shutdown()
        self._executor = None

    def start(
        self,
        addresses: list[str] | None = None,
        require_token: bool | None = None,
        require_tls: bool | None = None,
        tls_cert_file: str | None = None,
        tls_key_file: str | None = None,
        require_on_loopback: bool | None = None,
    ) -> None:
        if self.is_running():
            return
        if self._servers or self._executor is not None:
            self._cleanup_held()
        entries = (
            list(addresses)
            if addresses is not None
            else config.listen_addresses()
        )
        if not entries:
            entries = list(config.listen_addresses())
        req_token = (
            config.require_token()
            if require_token is None
            else bool(require_token)
        )
        req_tls = (
            config.require_tls()
            if require_tls is None
            else bool(require_tls)
        )
        req_on_loopback = (
            config.require_on_loopback()
            if require_on_loopback is None
            else bool(require_on_loopback)
        )
        cert_file = (
            config.tls_cert_file()
            if tls_cert_file is None
            else tls_cert_file
        )
        key_file = (
            config.tls_key_file()
            if tls_key_file is None
            else tls_key_file
        )
        try:
            targets = [
                (entry, *config.parse_listen_entry(entry))
                for entry in entries
            ]
        except ValueError as exc:
            FreeCAD.Console.PrintDeveloperError(
                f"{LOG_PREFIX} {exc} Change the listen address in Edit → "
                "Preferences → MCP Bridge.\n"
            )
            raise
        non_loopback = [
            entry
            for entry in entries
            if not config.is_loopback_address(entry)
        ]
        protected = entries if req_on_loopback else non_loopback
        ssl_context = None
        if protected and req_tls:
            try:
                ssl_context = config.build_ssl_context(cert_file, key_file)
            except ValueError as exc:
                FreeCAD.Console.PrintDeveloperError(f"{LOG_PREFIX} {exc}\n")
                raise
        if protected and req_token and not config.token_hash():
            msg = (
                "A bearer token is required and none has been generated. "
                "Turn the bridge on with MCP Bridge On/Off to generate "
                "one, or click Generate new token in Edit → Preferences "
                "→ MCP Bridge, or untick Require bearer token there."
            )
            FreeCAD.Console.PrintDeveloperError(f"{LOG_PREFIX} {msg}\n")
            raise ValueError(msg)
        # Created here so it inherits main-thread affinity (start() is called
        # from the toolbar toggle / GUI startup, both on the main thread).
        self._executor = Executor()

        bound_servers = []
        failed_entry = entries[0]
        try:
            for entry, host, bind_port in targets:
                failed_entry = entry
                is_loopback = config.is_loopback_address(entry)
                protect_entry = req_on_loopback or not is_loopback
                srv = paging.MaintenanceHTTPServer(
                    (host, bind_port), McpRequestHandler
                )
                bound_servers.append(srv)
                srv.executor = self._executor
                srv.require_token = bool(req_token and protect_entry)
                if ssl_context is not None and protect_entry:
                    srv.socket = ssl_context.wrap_socket(
                        srv.socket,
                        server_side=True,
                        do_handshake_on_connect=False,
                    )
        except OSError as exc:
            for srv in bound_servers:
                srv.server_close()
            if self._executor is not None:
                self._executor.shutdown()
            self._servers = []
            self._executor = None
            FreeCAD.Console.PrintDeveloperError(
                f"{LOG_PREFIX} Could not bind {failed_entry} ({exc}). "
                "Change the listen address in Edit → Preferences → "
                "MCP Bridge.\n"
            )
            raise

        started_servers = []
        try:
            for index, srv in enumerate(bound_servers):
                name = (
                    "mcp-bridge-http"
                    if index == 0
                    else f"mcp-bridge-http-{index}"
                )
                thread = threading.Thread(
                    target=srv.serve_forever,
                    name=name,
                    daemon=True,
                )
                thread.start()
                started_servers.append((srv, thread))
        except Exception:
            started_set = {srv for srv, _ in started_servers}
            for srv, _ in started_servers:
                srv.shutdown()
                srv.server_close()
            for srv in bound_servers:
                if srv not in started_set:
                    srv.server_close()
            if self._executor is not None:
                self._executor.shutdown()
            self._servers = []
            self._executor = None
            raise

        self._servers = started_servers
        endpoints_with_path = ", ".join(
            f"{entry}{ENDPOINT_PATH}" for entry in entries
        )
        FreeCAD.Console.PrintMessage(
            f"{LOG_PREFIX} HTTP server listening on {endpoints_with_path}\n"
        )
        if non_loopback:
            FreeCAD.Console.PrintWarning(
                _non_loopback_warning(
                    entries, non_loopback, req_token, req_tls
                )
            )

    def stop(self) -> None:
        self._cleanup_held()
        paging.clear_all()
        FreeCAD.Console.PrintMessage(f"{LOG_PREFIX} HTTP server stopped\n")
