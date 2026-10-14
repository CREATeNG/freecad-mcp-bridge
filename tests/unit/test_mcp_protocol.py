"""Requirements on the MCP protocol handling (mcp_protocol.py).

Each test name, and the first line of its docstring, states one requirement
as an MCP client connecting to the bridge experiences it.
"""

import base64
import json
import re
import unittest
from email.message import Message
from pathlib import Path

from freecad.mcp_bridge import mcp_protocol
from freecad.mcp_bridge.constants import PROTOCOL_VERSION

ROOT = Path(__file__).resolve().parents[2]
VERSION = "2026-07-28"
LEGACY_VERSION_HEADER = {"MCP-Protocol-Version": "2025-11-25"}
CLIENT_META = {
    "io.modelcontextprotocol/protocolVersion": VERSION,
    "io.modelcontextprotocol/clientInfo": {"name": "unit", "version": "0"},
    "io.modelcontextprotocol/clientCapabilities": {},
}


def package_version():
    text = (ROOT / "package.xml").read_text(encoding="utf-8")
    return re.search(r"<version>([^<]+)</version>", text).group(1)


def request(method, params=None, req_id=1):
    """A valid 2026-07-28 request: params carry the client's _meta."""
    params = {"_meta": dict(CLIENT_META), **(params or {})}
    return {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}


def headers_for(message, **overrides):
    """The HTTP headers a 2026-07-28 client sends with `message`.

    An override of None leaves that header out.
    """
    values = {
        "MCP-Protocol-Version": VERSION,
        "Mcp-Method": message["method"],
    }
    if message["method"] == "tools/call":
        values["Mcp-Name"] = message["params"]["name"]
    values.update(overrides)
    headers = Message()  # matches names case-insensitively, as http.server
    for name, value in values.items():
        if value is not None:
            headers[name] = value
    return headers


def tool_call(name="execute_python"):
    return request("tools/call", {"name": name, "arguments": {}})


class RequestCheckTests(unittest.TestCase):
    def assertRejected(self, rejection, status, code):
        self.assertIsNotNone(rejection)
        got_status, response = rejection
        self.assertEqual(got_status, status)
        self.assertEqual(response["error"]["code"], code)
        self.assertEqual(response["id"], 1)
        return response["error"]

    def test_the_bridge_speaks_protocol_version_2026_07_28(self):
        """The bridge speaks MCP protocol version 2026-07-28."""
        self.assertEqual(PROTOCOL_VERSION, VERSION)

    def test_a_valid_2026_07_28_request_is_accepted(self):
        """A request with matching version header and _meta, client capabilities and method and name headers is accepted."""
        for message in (request("tools/list"), tool_call()):
            with self.subTest(method=message["method"]):
                self.assertIsNone(
                    mcp_protocol.check_request(message, headers_for(message))
                )

    def test_header_names_match_whatever_their_case(self):
        """Header names match whatever their case."""
        message = tool_call()
        headers = Message()
        headers["mcp-protocol-version"] = VERSION
        headers["MCP-METHOD"] = "tools/call"
        headers["mcp-name"] = "execute_python"
        self.assertIsNone(mcp_protocol.check_request(message, headers))

    def test_an_initialize_request_gets_the_unsupported_version_error_naming_2026_07_28(self):
        """An initialize request gets 400 and the unsupported-version error, naming 2026-07-28 as the one supported version."""
        message = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "legacy", "version": "0"},
            },
        }
        error = self.assertRejected(
            mcp_protocol.check_request(message, Message()), 400, -32022
        )
        self.assertEqual(
            error["data"], {"supported": [VERSION], "requested": "2025-11-25"}
        )

    def test_another_protocol_version_gets_the_unsupported_version_error(self):
        """A request in another protocol version gets 400 and the unsupported-version error, naming 2026-07-28."""
        message = request("tools/list")
        message["params"]["_meta"][
            "io.modelcontextprotocol/protocolVersion"
        ] = "2025-11-25"
        headers = headers_for(message, **LEGACY_VERSION_HEADER)
        error = self.assertRejected(
            mcp_protocol.check_request(message, headers), 400, -32022
        )
        self.assertEqual(
            error["data"], {"supported": [VERSION], "requested": "2025-11-25"}
        )

    def test_a_legacy_request_with_only_the_version_header_gets_the_unsupported_version_error(self):
        """A request with a legacy version header and no _meta gets 400 and the unsupported-version error."""
        message = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        headers = headers_for(message, **LEGACY_VERSION_HEADER)
        self.assertRejected(
            mcp_protocol.check_request(message, headers), 400, -32022
        )

    def test_a_missing_protocol_version_header_is_a_header_mismatch(self):
        """A request with no MCP-Protocol-Version header gets 400 and a header-mismatch error."""
        message = request("tools/list")
        headers = headers_for(message, **{"MCP-Protocol-Version": None})
        self.assertRejected(
            mcp_protocol.check_request(message, headers), 400, -32020
        )

    def test_a_version_header_differing_from_meta_is_a_header_mismatch(self):
        """A request whose MCP-Protocol-Version header differs from its _meta version gets 400 and a header-mismatch error."""
        message = request("tools/list")
        headers = headers_for(message, **LEGACY_VERSION_HEADER)
        self.assertRejected(
            mcp_protocol.check_request(message, headers), 400, -32020
        )

    def test_a_request_missing_required_meta_gets_invalid_params(self):
        """A request whose _meta lacks the protocol version or client capabilities gets 400 and invalid params."""
        for key in (
            "io.modelcontextprotocol/protocolVersion",
            "io.modelcontextprotocol/clientCapabilities",
        ):
            with self.subTest(missing=key):
                message = request("tools/list")
                del message["params"]["_meta"][key]
                self.assertRejected(
                    mcp_protocol.check_request(message, headers_for(message)),
                    400,
                    -32602,
                )
        message = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        with self.subTest(missing="_meta"):
            self.assertRejected(
                mcp_protocol.check_request(message, headers_for(message)),
                400,
                -32602,
            )

    def test_a_missing_or_differing_mcp_method_header_is_a_header_mismatch(self):
        """A request whose Mcp-Method header is missing or differs from its method gets 400 and a header-mismatch error."""
        message = request("tools/list")
        for value in (None, "server/discover"):
            with self.subTest(header=value):
                headers = headers_for(message, **{"Mcp-Method": value})
                self.assertRejected(
                    mcp_protocol.check_request(message, headers), 400, -32020
                )

    def test_a_tool_call_with_a_missing_or_differing_mcp_name_header_is_a_header_mismatch(self):
        """A tool call whose Mcp-Name header is missing, malformed or differs from the tool name gets 400 and a header-mismatch error."""
        message = tool_call()
        for value in (None, "get_output_page", "=?base64?%%%?="):
            with self.subTest(header=value):
                headers = headers_for(message, **{"Mcp-Name": value})
                self.assertRejected(
                    mcp_protocol.check_request(message, headers), 400, -32020
                )

    def test_a_base64_encoded_mcp_name_header_is_decoded_before_matching(self):
        """A Base64-encoded Mcp-Name header is decoded before it is matched against the tool name."""
        message = tool_call()
        encoded = base64.b64encode(b"execute_python").decode("ascii")
        headers = headers_for(message, **{"Mcp-Name": f"=?base64?{encoded}?="})
        self.assertIsNone(mcp_protocol.check_request(message, headers))


class AnswerTests(unittest.TestCase):
    def test_discover_answers_with_the_supported_version_tools_capability_and_server_info(self):
        """server/discover answers with 2026-07-28 as the one supported version, the tools capability, caching hints and the server's name and version."""
        status, response = mcp_protocol.handle_request(
            request("server/discover")
        )
        self.assertEqual(status, 200)
        self.assertEqual(response["id"], 1)
        result = response["result"]
        self.assertEqual(result["resultType"], "complete")
        self.assertEqual(result["supportedVersions"], [VERSION])
        self.assertEqual(result["capabilities"], {"tools": {}})
        self.assertGreaterEqual(result["ttlMs"], 0)
        self.assertIn(result["cacheScope"], ("public", "private"))
        self.assertEqual(
            result["_meta"]["io.modelcontextprotocol/serverInfo"],
            {"name": "freecad-mcp-bridge", "version": package_version()},
        )

    def test_tools_list_returns_exactly_the_three_tools_with_caching_hints(self):
        """tools/list returns exactly execute_python, execute_python_file and get_output_page, with resultType and caching hints."""
        status, response = mcp_protocol.handle_request(request("tools/list"))
        self.assertEqual(status, 200)
        result = response["result"]
        names = [tool["name"] for tool in result["tools"]]
        self.assertEqual(
            sorted(names),
            ["execute_python", "execute_python_file", "get_output_page"],
        )
        self.assertEqual(result["resultType"], "complete")
        self.assertGreaterEqual(result["ttlMs"], 0)
        self.assertIn(result["cacheScope"], ("public", "private"))

    def test_a_message_with_no_id_is_a_notification(self):
        """Any message with no id is a notification, whatever its method."""
        for method in ("notifications/cancelled", "tools/list", "tools/call"):
            with self.subTest(method=method):
                message = {"jsonrpc": "2.0", "method": method}
                self.assertTrue(mcp_protocol.is_notification(message))
        self.assertFalse(mcp_protocol.is_notification(request("tools/list")))

    def test_an_unknown_method_gets_404_and_method_not_found(self):
        """An unknown method, ping among them, gets 404 and a JSON-RPC method-not-found error."""
        for method in ("no/such/method", "ping"):
            with self.subTest(method=method):
                status, response = mcp_protocol.handle_request(
                    request(method, req_id=3)
                )
                self.assertEqual(status, 404)
                self.assertEqual(response["id"], 3)
                self.assertEqual(response["error"]["code"], -32601)
                self.assertNotIn("result", response)

    def test_listen_is_acknowledged_with_no_notifications_and_closed_at_once(self):
        """subscriptions/listen is acknowledged with no notification types, then closed gracefully with nothing sent between."""
        message = request(
            "subscriptions/listen",
            {"notifications": {"toolsListChanged": True}},
            req_id="listen-1",
        )
        acknowledged, closed = mcp_protocol.listen_messages(message)
        self.assertEqual(
            acknowledged["method"], "notifications/subscriptions/acknowledged"
        )
        self.assertNotIn("id", acknowledged)
        self.assertEqual(acknowledged["params"]["notifications"], {})
        subscription = "io.modelcontextprotocol/subscriptionId"
        self.assertEqual(
            acknowledged["params"]["_meta"][subscription], "listen-1"
        )
        self.assertEqual(closed["id"], "listen-1")
        self.assertEqual(closed["result"]["resultType"], "complete")
        self.assertEqual(closed["result"]["_meta"][subscription], "listen-1")


class ToolResultTests(unittest.TestCase):
    def test_a_tool_result_is_one_json_text_block_marked_complete(self):
        """A tool result is one JSON text block, with resultType complete and no error flag."""
        response = mcp_protocol.tool_call_response(
            5, {"page": [], "has_more": False, "job_token": "t", "page_no": 0}
        )
        result = response["result"]
        self.assertEqual(result["resultType"], "complete")
        self.assertNotIn("isError", result)
        (block,) = result["content"]
        self.assertEqual(block["type"], "text")
        self.assertEqual(
            json.loads(block["text"]),
            {"page": [], "has_more": False, "page_no": 0, "job_token": "t"},
        )

    def test_a_failed_tool_call_is_flagged_is_error_and_keeps_its_payload(self):
        """A failed tool call is flagged isError, and its payload still carries the error."""
        response = mcp_protocol.tool_call_response(
            5, {"page": [], "has_more": False, "error": "page_no out of range"}
        )
        result = response["result"]
        self.assertIs(result["isError"], True)
        payload = json.loads(result["content"][0]["text"])
        self.assertEqual(payload["error"], "page_no out of range")


if __name__ == "__main__":
    unittest.main()
