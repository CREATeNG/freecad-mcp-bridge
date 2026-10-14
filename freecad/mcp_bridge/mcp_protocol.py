"""MCP protocol handling for protocol version 2026-07-28, the only one spoken.

check_request() validates each request's protocol version, its `_meta` and
the HTTP headers that mirror its body, and rejects an `initialize` from the
handshake-based versions. handle_request() answers server/discover and
tools/list; listen_messages() answers subscriptions/listen. The HTTP layer
dispatches tools/call itself, since it needs the Executor and paging buffer,
and frames its replies with tool_call_response() and error_response().
check_request() and handle_request() return the HTTP status with the reply.
"""

import base64
import binascii
import json

from freecad.mcp_bridge.constants import PROTOCOL_VERSION, SERVER_NAME
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
    """Return (status, error response) for a request to reject, else None.

    `request` is a parsed JSON-RPC request (not a notification); `headers`
    are its HTTP headers, whose get() matches names case-insensitively.
    """
    req_id = request.get("id")
    method = request.get("method")
    params = request.get("params")
    if not isinstance(method, str):
        return 400, _error(req_id, INVALID_REQUEST, "Invalid request")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return 400, _error(req_id, INVALID_PARAMS, "params must be an object")
    if method == "initialize":
        # The handshake-based versions open with initialize. Name the
        # version this server speaks: it may be all such a client can show.
        requested = params.get("protocolVersion")
        if not isinstance(requested, str):
            requested = ""
        return _unsupported(req_id, requested)
    meta = params.get("_meta")
    if not isinstance(meta, dict):
        meta = {}

    header_version = headers.get("MCP-Protocol-Version")
    body_version = meta.get(META_PROTOCOL_VERSION)
    if header_version is None:
        return _mismatch(req_id, "MCP-Protocol-Version header is missing")
    if body_version is not None and body_version != header_version:
        return _mismatch(
            req_id,
            f"MCP-Protocol-Version header value '{header_version}' does "
            f"not match body value '{body_version}'",
        )
    if header_version != PROTOCOL_VERSION:
        return _unsupported(req_id, header_version)
    if body_version is None:
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


def handle_request(request: dict):
    """Return (status, response) for an accepted server/discover, tools/list
    or unknown-method request; check_request has already passed it."""
    method = request.get("method")
    req_id = request.get("id")

    if method == "server/discover":
        return 200, _result(req_id, _discover_result())
    if method == "tools/list":
        return 200, _result(
            req_id,
            {
                "tools": TOOL_DEFINITIONS,
                "ttlMs": CACHE_TTL_MS,
                "cacheScope": CACHE_SCOPE,
            },
        )
    return 404, _error(req_id, METHOD_NOT_FOUND, f"Method not found: {method}")


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
    closed = _result(req_id, {"_meta": {META_SUBSCRIPTION_ID: req_id}})
    return [acknowledged, closed]


def _discover_result() -> dict:
    return {
        "supportedVersions": [PROTOCOL_VERSION],
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


def _result(req_id, result) -> dict:
    """A result response: resultType "complete", and serverInfo in _meta."""
    meta = {
        META_SERVER_INFO: {"name": SERVER_NAME, "version": addon_version()}
    }
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
        {"supported": [PROTOCOL_VERSION], "requested": requested},
    )


def tool_call_response(req_id, result) -> dict:
    """Wrap a tool result ({page, has_more, [page_no], [job_token], [error]})
    as an MCP tools/call result — a single JSON text content block. A result
    carrying an `error` is a failed tool call, flagged isError."""
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
    result = {"content": [{"type": "text", "text": json.dumps(payload)}]}
    if "error" in payload:
        result["isError"] = True
    return _result(req_id, result)


def error_response(req_id, code, message) -> dict:
    """Public JSON-RPC error envelope, for the transport layer."""
    return _error(req_id, code, message)
