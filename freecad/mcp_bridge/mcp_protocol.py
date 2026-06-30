"""MCP protocol handling — maps parsed JSON-RPC requests to responses.

Pure protocol logic: no FreeCAD or HTTP dependency, so it runs and is
testable on plain CPython. The execute path (tools/call) is added in a
later slice; for now only the lifecycle methods are answered.
"""

from freecad.mcp_bridge.constants import PROTOCOL_VERSION, SERVER_NAME
from freecad.mcp_bridge.resources import addon_version
from freecad.mcp_bridge.tools import TOOL_DEFINITIONS

METHOD_NOT_FOUND = -32601


def handle_request(request: dict):
    """Return a JSON-RPC response dict, or None for a notification.

    `request` is the already-parsed JSON-RPC object.
    """
    method = request.get("method")
    is_notification = "id" not in request
    req_id = request.get("id")

    if method == "initialize":
        return _result(req_id, _initialize_result())
    if method == "tools/list":
        return _result(req_id, {"tools": TOOL_DEFINITIONS})
    if is_notification:
        # Notifications (e.g. notifications/initialized) get no response.
        return None
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
