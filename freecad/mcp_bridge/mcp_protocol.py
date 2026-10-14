"""MCP protocol handling — maps parsed JSON-RPC requests to responses.

Handles the lifecycle methods (initialize, tools/list) and provides the
tools/call response-framing helpers (tool_call_response, error_response);
the HTTP layer performs the actual tools/call dispatch since it needs the
Executor and paging buffer.
"""

import json

from freecad.mcp_bridge.constants import PROTOCOL_VERSION, SERVER_NAME
from freecad.mcp_bridge.resources import addon_version
from freecad.mcp_bridge.tools import TOOL_DEFINITIONS

METHOD_NOT_FOUND = -32601


def is_notification(request) -> bool:
    """A JSON-RPC message with no `id` member is a notification.

    It gets no reply.
    """
    return isinstance(request, dict) and "id" not in request


def handle_request(request: dict) -> dict:
    """Return the JSON-RPC response dict for a request (not a notification).

    `request` is the already-parsed JSON-RPC object. Callers check
    is_notification first, so a notification never reaches here.
    """
    method = request.get("method")
    req_id = request.get("id")

    if method == "initialize":
        return _result(req_id, _initialize_result())
    if method == "tools/list":
        return _result(req_id, {"tools": TOOL_DEFINITIONS})
    return _error(req_id, METHOD_NOT_FOUND, f"Method not found: {method}")


def _initialize_result() -> dict:
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {"tools": {}},
        "serverInfo": {"name": SERVER_NAME, "version": addon_version()},
    }


def _result(req_id, result) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error(req_id, code, message) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": code, "message": message},
    }


def tool_call_response(req_id, result) -> dict:
    """Wrap a tool result ({page, has_more, [page_no], [job_token], [error]})
    as an MCP tools/call result — a single JSON text content block."""
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
    return _result(req_id, result)


def error_response(req_id, code, message) -> dict:
    """Public JSON-RPC error envelope, for the transport layer."""
    return _error(req_id, code, message)
