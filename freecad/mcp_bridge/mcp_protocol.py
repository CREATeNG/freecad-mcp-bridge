"""MCP protocol handling for protocol versions 2026-07-28 and 2025-11-25.

check_request() decides which version each request is in, from its HTTP
headers and `_meta`, and validates it: a 2026-07-28 request carries its
version in `_meta` and headers that mirror its body; `initialize` opens the
2025-11-25 handshake, and later 2025-11-25 requests carry only the version
header. handle_request() answers server/discover and tools/list in
2026-07-28, and initialize, tools/list and ping in 2025-11-25;
listen_messages() answers subscriptions/listen. The HTTP layer dispatches
tools/call itself, since it needs the Executor and paging buffer, and frames
its replies with tool_call_response() and error_response().
check_request() and handle_request() return the HTTP status with the reply.
"""

import base64
import binascii
import json

from freecad.mcp_bridge.constants import (
    LEGACY_PROTOCOL_VERSION,
    PROTOCOL_VERSION,
    SERVER_NAME,
    SUPPORTED_PROTOCOL_VERSIONS,
)
from freecad.mcp_bridge.resources import addon_version
from freecad.mcp_bridge.tools import TOOL_DEFINITIONS

INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
HEADER_MISMATCH = -32020
UNSUPPORTED_PROTOCOL_VERSION = -32022

META_PROTOCOL_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CLIENT_CAPABILITIES = "io.modelcontextprotocol/clientCapabilities"
META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"
META_SUBSCRIPTION_ID = "io.modelcontextprotocol/subscriptionId"

# The body field each method's Mcp-Name header mirrors.
_NAMED_METHODS = {
    "tools/call": "name",
    "resources/read": "uri",
    "prompts/get": "name",
}
_BASE64_PREFIX = "=?base64?"
_BASE64_SUFFIX = "?="

# Caching hints on server/discover and tools/list: immediately stale, so a
# client re-asks whenever it needs them (a loopback round trip), and never
# holds an answer across a FreeCAD restart or an addon update. Both answers
# are the same for every caller.
CACHE_TTL_MS = 0
CACHE_SCOPE = "public"


def is_notification(request) -> bool:
    """A JSON-RPC message with no `id` member is a notification.

    It gets no reply.
    """
    return isinstance(request, dict) and "id" not in request


def check_request(request: dict, headers):
    """Return (version, None) for a request to serve in that protocol
    version, or (None, (status, error response)) for one to reject.

    `request` is a parsed JSON-RPC request (not a notification); `headers`
    are its HTTP headers, whose get() matches names case-insensitively.
    """
    req_id = request.get("id")
    method = request.get("method")
    params = request.get("params")
    if not isinstance(method, str):
        return None, (400, _error(req_id, INVALID_REQUEST, "Invalid request"))
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return None, (
            400,
            _error(req_id, INVALID_PARAMS, "params must be an object"),
        )
    if method == "initialize":
        # initialize opens the 2025-11-25 handshake, whatever version it
        # asks for: the answer offers 2025-11-25.
        return LEGACY_PROTOCOL_VERSION, None
    meta = params.get("_meta")
    if not isinstance(meta, dict):
        meta = {}

    header_version = headers.get("MCP-Protocol-Version")
    body_version = meta.get(META_PROTOCOL_VERSION)
    if header_version is None:
        # A request without the header is in a version before 2025-06-18,
        # which this server does not speak.
        return None, _mismatch(
            req_id, "MCP-Protocol-Version header is missing"
        )
    if body_version is not None and body_version != header_version:
        return None, _mismatch(
            req_id,
            f"MCP-Protocol-Version header value '{header_version}' does "
            f"not match body value '{body_version}'",
        )
    if header_version == LEGACY_PROTOCOL_VERSION and body_version is None:
        return LEGACY_PROTOCOL_VERSION, None
    if header_version != PROTOCOL_VERSION:
        # Only 2026-07-28 carries its version in _meta, so a 2025-11-25
        # request that does is asking for a version not spoken here.
        return None, _unsupported(req_id, header_version)
    rejection = _check_2026_request(req_id, method, params, meta, headers)
    if rejection is not None:
        return None, rejection
    return PROTOCOL_VERSION, None


def _check_2026_request(req_id, method, params, meta, headers):
    """Return (status, error response) for a 2026-07-28 request whose
    `_meta`, or method and name headers, fail validation, else None."""
    if meta.get(META_PROTOCOL_VERSION) is None:
        return 400, _error(
            req_id, INVALID_PARAMS, f"_meta is missing {META_PROTOCOL_VERSION}"
        )
    if not isinstance(meta.get(META_CLIENT_CAPABILITIES), dict):
        return 400, _error(
            req_id,
            INVALID_PARAMS,
            f"_meta is missing {META_CLIENT_CAPABILITIES}",
        )

    header_method = headers.get("Mcp-Method")
    if header_method is None:
        return _mismatch(req_id, "Mcp-Method header is missing")
    if header_method != method:
        return _mismatch(
            req_id,
            f"Mcp-Method header value '{header_method}' does not match "
            f"body value '{method}'",
        )
    field = _NAMED_METHODS.get(method)
    if field is not None:
        raw_name = headers.get("Mcp-Name")
        if raw_name is None:
            return _mismatch(req_id, "Mcp-Name header is missing")
        header_name = _decode_header(raw_name)
        if header_name is None:
            return _mismatch(req_id, "Mcp-Name header value is malformed")
        if header_name != params.get(field):
            return _mismatch(
                req_id,
                f"Mcp-Name header value '{header_name}' does not match "
                f"body value '{params.get(field)}'",
            )
    return None


def handle_request(request: dict, version: str):
    """Return (status, response) for a request other than tools/call and
    subscriptions/listen, which check_request has accepted in `version`."""
    method = request.get("method")
    req_id = request.get("id")
    if version == LEGACY_PROTOCOL_VERSION:
        return _handle_legacy_request(req_id, method)

    if method == "server/discover":
        return 200, _result(req_id, _discover_result(), version)
    if method == "tools/list":
        return 200, _result(
            req_id,
            {
                "tools": TOOL_DEFINITIONS,
                "ttlMs": CACHE_TTL_MS,
                "cacheScope": CACHE_SCOPE,
            },
            version,
        )
    return 404, _error(req_id, METHOD_NOT_FOUND, f"Method not found: {method}")


def _handle_legacy_request(req_id, method):
    """Answer a 2025-11-25 request. That version gives HTTP 404 a session
    meaning, so an unknown method gets method-not-found with 200."""
    if method == "initialize":
        result = {
            "protocolVersion": LEGACY_PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": _server_info(),
        }
    elif method == "tools/list":
        result = {"tools": TOOL_DEFINITIONS}
    elif method == "ping":
        result = {}
    else:
        return 200, _error(
            req_id, METHOD_NOT_FOUND, f"Method not found: {method}"
        )
    return 200, _result(req_id, result, LEGACY_PROTOCOL_VERSION)


def listen_messages(request: dict) -> list:
    """The messages answering a subscriptions/listen request, in order.

    The server sends no notifications, so it honours none of the requested
    types: it acknowledges the subscription with an empty filter, then ends
    it at once with the graceful-closure result. The HTTP layer streams
    both as SSE events.
    """
    req_id = request.get("id")
    acknowledged = {
        "jsonrpc": "2.0",
        "method": "notifications/subscriptions/acknowledged",
        "params": {
            "_meta": {META_SUBSCRIPTION_ID: req_id},
            "notifications": {},
        },
    }
    closed = _result(
        req_id, {"_meta": {META_SUBSCRIPTION_ID: req_id}}, PROTOCOL_VERSION
    )
    return [acknowledged, closed]


def _discover_result() -> dict:
    return {
        "supportedVersions": list(SUPPORTED_PROTOCOL_VERSIONS),
        "capabilities": {"tools": {}},
        "ttlMs": CACHE_TTL_MS,
        "cacheScope": CACHE_SCOPE,
    }


def _decode_header(value):
    """A header value, Base64-decoded if it carries the sentinel; None if
    the encoding is malformed."""
    if not (
        value.startswith(_BASE64_PREFIX) and value.endswith(_BASE64_SUFFIX)
    ):
        return value
    encoded = value[len(_BASE64_PREFIX):-len(_BASE64_SUFFIX)]
    try:
        return base64.b64decode(encoded, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None


def _server_info() -> dict:
    return {"name": SERVER_NAME, "version": addon_version()}


def _result(req_id, result, version) -> dict:
    """A result response in `version`. A 2026-07-28 result carries
    resultType "complete", and serverInfo in _meta."""
    if version == PROTOCOL_VERSION:
        meta = {META_SERVER_INFO: _server_info()}
        meta.update(result.get("_meta", {}))
        result = {**result, "resultType": "complete", "_meta": meta}
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error(req_id, code, message, data=None) -> dict:
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": error}


def _mismatch(req_id, message):
    return 400, _error(req_id, HEADER_MISMATCH, f"Header mismatch: {message}")


def _unsupported(req_id, requested):
    return 400, _error(
        req_id,
        UNSUPPORTED_PROTOCOL_VERSION,
        "Unsupported protocol version",
        {
            "supported": list(SUPPORTED_PROTOCOL_VERSIONS),
            "requested": requested,
        },
    )


def tool_result_payload(result) -> dict:
    """The tool result an agent reads, before the content-block wrap."""
    payload = {
        "page": result.get("page", []),
        "has_more": result.get("has_more", False),
    }
    if result.get("page_no") is not None:
        payload["page_no"] = result["page_no"]
    if result.get("job_token"):
        payload["job_token"] = result["job_token"]
    if result.get("error"):
        payload["error"] = result["error"]
    return payload


def serialized_page_length(page, page_no, job_token) -> int:
    """Character length of the tool result for this page.

    ``has_more`` is false, the longer spelling, so a page that fits
    here still fits when the job ends. ``page_no`` is the number the
    page will carry.
    """
    payload = tool_result_payload({
        "page": page,
        "has_more": False,
        "page_no": page_no,
        "job_token": job_token,
    })
    return len(json.dumps(payload))


def tool_call_response(req_id, result, version) -> dict:
    """Wrap a tool result ({page, has_more, [page_no], [job_token], [error]})
    as an MCP tools/call result in `version` — a single JSON text content
    block. A result carrying an `error` is a failed tool call, flagged
    isError."""
    payload = tool_result_payload(result)
    wrapped = {"content": [{"type": "text", "text": json.dumps(payload)}]}
    if "error" in payload:
        wrapped["isError"] = True
    return _result(req_id, wrapped, version)


def error_response(req_id, code, message) -> dict:
    """Public JSON-RPC error envelope, for the transport layer."""
    return _error(req_id, code, message)
