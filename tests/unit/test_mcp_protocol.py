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
from freecad.mcp_bridge.constants import (
    LEGACY_PROTOCOL_VERSION,
    PROTOCOL_VERSION,
)

ROOT = Path(__file__).resolve().parents[2]
VERSION = "2026-07-28"
NOVEMBER = "2025-11-25"
BOTH_VERSIONS = [VERSION, NOVEMBER]
LEGACY_VERSION_HEADER = {"MCP-Protocol-Version": NOVEMBER}
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


def november_request(method, params=None, req_id=1):
    """A 2025-11-25 request: no _meta; params only if given."""
    message = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def november_headers():
    """The HTTP headers a 2025-11-25 client sends after initialize."""
    headers = Message()
    headers["MCP-Protocol-Version"] = NOVEMBER
    return headers


def initialize(requested=NOVEMBER):
    return november_request(
        "initialize",
        {
            "protocolVersion": requested,
            "capabilities": {},
            "clientInfo": {"name": "november", "version": "0"},
        },
    )


class RequestCheckTests(unittest.TestCase):
    def assertRejected(self, checked, status, code):
        version, rejection = checked
        self.assertIsNone(version)
        self.assertIsNotNone(rejection)
        got_status, response = rejection
        self.assertEqual(got_status, status)
        self.assertEqual(response["error"]["code"], code)
        self.assertEqual(response["id"], 1)
        return response["error"]

    def assertAccepted(self, checked, version):
        self.assertEqual(checked, (version, None))

    def test_the_bridge_speaks_protocol_versions_2026_07_28_and_2025_11_25(self):
        """The bridge speaks exactly two MCP protocol versions, 2026-07-28 and 2025-11-25."""
        self.assertEqual(PROTOCOL_VERSION, VERSION)
        self.assertEqual(LEGACY_PROTOCOL_VERSION, NOVEMBER)

    def test_a_valid_2026_07_28_request_is_accepted(self):
        """A request with matching version header and _meta, client capabilities and method and name headers is accepted as 2026-07-28."""
        for message in (request("tools/list"), tool_call()):
            with self.subTest(method=message["method"]):
                self.assertAccepted(
                    mcp_protocol.check_request(message, headers_for(message)),
                    VERSION,
                )

    def test_header_names_match_whatever_their_case(self):
        """Header names match whatever their case."""
        message = tool_call()
        headers = Message()
        headers["mcp-protocol-version"] = VERSION
        headers["MCP-METHOD"] = "tools/call"
        headers["mcp-name"] = "execute_python"
        self.assertAccepted(
            mcp_protocol.check_request(message, headers), VERSION
        )

    def test_an_initialize_request_opens_the_2025_11_25_handshake_whatever_its_headers(self):
        """An initialize request is accepted as 2025-11-25, with or without version headers and whatever version it asks for."""
        for requested in (NOVEMBER, "2025-06-18", "2025-03-26", VERSION):
            for header in (None, NOVEMBER):
                headers = Message() if header is None else november_headers()
                with self.subTest(requested=requested, header=header):
                    self.assertAccepted(
                        mcp_protocol.check_request(
                            initialize(requested), headers
                        ),
                        NOVEMBER,
                    )

    def test_a_2025_11_25_request_with_the_version_header_and_no_meta_version_is_accepted(self):
        """A request with the 2025-11-25 version header and no _meta protocol version is accepted as 2025-11-25, needing no method or name headers."""
        messages = (
            november_request("tools/list"),
            november_request("ping"),
            november_request(
                "tools/call", {"name": "execute_python", "arguments": {}}
            ),
            november_request("tools/list", {"_meta": {"progressToken": 1}}),
        )
        for message in messages:
            with self.subTest(message=message):
                self.assertAccepted(
                    mcp_protocol.check_request(message, november_headers()),
                    NOVEMBER,
                )

    def test_another_protocol_version_gets_the_unsupported_version_error_naming_both(self):
        """A request in another protocol version gets 400 and the unsupported-version error, naming 2026-07-28 and 2025-11-25."""
        for version in ("2025-06-18", "2025-03-26", "2099-01-01"):
            with self.subTest(version=version):
                message = november_request("tools/list")
                headers = Message()
                headers["MCP-Protocol-Version"] = version
                error = self.assertRejected(
                    mcp_protocol.check_request(message, headers), 400, -32022
                )
                self.assertEqual(
                    error["data"],
                    {"supported": BOTH_VERSIONS, "requested": version},
                )

    def test_a_2025_11_25_request_carrying_the_version_in_meta_gets_the_unsupported_version_error(self):
        """A request carrying 2025-11-25 in _meta as well as the header gets 400 and the unsupported-version error: only 2026-07-28 is spoken per request."""
        message = request("tools/list")
        message["params"]["_meta"][
            "io.modelcontextprotocol/protocolVersion"
        ] = NOVEMBER
        headers = headers_for(message, **LEGACY_VERSION_HEADER)
        error = self.assertRejected(
            mcp_protocol.check_request(message, headers), 400, -32022
        )
        self.assertEqual(
            error["data"], {"supported": BOTH_VERSIONS, "requested": NOVEMBER}
        )

    def test_a_missing_protocol_version_header_is_a_header_mismatch(self):
        """A request other than initialize with no MCP-Protocol-Version header gets 400 and a header-mismatch error, in either version's form."""
        message = request("tools/list")
        headers = headers_for(message, **{"MCP-Protocol-Version": None})
        with self.subTest(form="2026-07-28"):
            self.assertRejected(
                mcp_protocol.check_request(message, headers), 400, -32020
            )
        for method in ("tools/list", "ping"):
            with self.subTest(form="2025-11-25", method=method):
                self.assertRejected(
                    mcp_protocol.check_request(
                        november_request(method), Message()
                    ),
                    400,
                    -32020,
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
        self.assertAccepted(
            mcp_protocol.check_request(message, headers), VERSION
        )


class AnswerTests(unittest.TestCase):
    def test_discover_answers_with_both_supported_versions_tools_capability_and_server_info(self):
        """server/discover answers with 2026-07-28 and 2025-11-25 as the supported versions, the tools capability, caching hints and the server's name and version."""
        status, response = mcp_protocol.handle_request(
            request("server/discover"), VERSION
        )
        self.assertEqual(status, 200)
        self.assertEqual(response["id"], 1)
        result = response["result"]
        self.assertEqual(result["resultType"], "complete")
        self.assertEqual(result["supportedVersions"], BOTH_VERSIONS)
        self.assertEqual(result["capabilities"], {"tools": {}})
        self.assertGreaterEqual(result["ttlMs"], 0)
        self.assertIn(result["cacheScope"], ("public", "private"))
        self.assertEqual(
            result["_meta"]["io.modelcontextprotocol/serverInfo"],
            {"name": "freecad-mcp-bridge", "version": package_version()},
        )

    def test_tools_list_returns_exactly_the_three_tools_with_caching_hints(self):
        """tools/list returns exactly execute_python, execute_python_file and get_output_page, with resultType and caching hints."""
        status, response = mcp_protocol.handle_request(
            request("tools/list"), VERSION
        )
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

    def test_each_tool_in_tools_list_carries_its_title_and_annotations(self):
        """Each tool in tools/list has its title and annotations."""
        expected = {
            "execute_python": (
                "Execute Python",
                {"readOnlyHint": False, "destructiveHint": True},
            ),
            "execute_python_file": (
                "Execute Python file",
                {"readOnlyHint": False, "destructiveHint": True},
            ),
            "get_output_page": (
                "Get output page",
                {"readOnlyHint": True},
            ),
        }
        for version, message in (
            (VERSION, request("tools/list")),
            (NOVEMBER, november_request("tools/list")),
        ):
            with self.subTest(version=version):
                _, response = mcp_protocol.handle_request(message, version)
                tools = {
                    tool["name"]: tool for tool in response["result"]["tools"]
                }
                self.assertEqual(set(tools), set(expected))
                for name, (title, annotations) in expected.items():
                    self.assertEqual(tools[name]["title"], title)
                    self.assertEqual(tools[name]["annotations"], annotations)

    def test_a_message_with_no_id_is_a_notification(self):
        """Any message with no id is a notification, whatever its method."""
        for method in ("notifications/cancelled", "tools/list", "tools/call"):
            with self.subTest(method=method):
                message = {"jsonrpc": "2.0", "method": method}
                self.assertTrue(mcp_protocol.is_notification(message))
        self.assertFalse(mcp_protocol.is_notification(request("tools/list")))

    def test_an_unknown_method_gets_404_and_method_not_found(self):
        """In 2026-07-28, an unknown method, ping among them, gets 404 and a JSON-RPC method-not-found error."""
        for method in ("no/such/method", "ping"):
            with self.subTest(method=method):
                status, response = mcp_protocol.handle_request(
                    request(method, req_id=3), VERSION
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


class NovemberAnswerTests(unittest.TestCase):
    def test_initialize_answers_2025_11_25_with_the_tools_capability_and_server_info(self):
        """initialize answers with protocol version 2025-11-25, the tools capability and the server's name and version."""
        status, response = mcp_protocol.handle_request(initialize(), NOVEMBER)
        self.assertEqual(status, 200)
        self.assertEqual(response["id"], 1)
        self.assertEqual(
            response["result"],
            {
                "protocolVersion": NOVEMBER,
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "freecad-mcp-bridge",
                    "version": package_version(),
                },
            },
        )

    def test_an_initialize_asking_for_another_version_gets_2025_11_25(self):
        """An initialize asking for an older version, or for 2026-07-28, gets 2025-11-25: the version the server offers in the handshake."""
        for requested in ("2025-06-18", "2025-03-26", "2024-11-05", VERSION):
            with self.subTest(requested=requested):
                _, response = mcp_protocol.handle_request(
                    initialize(requested), NOVEMBER
                )
                self.assertEqual(
                    response["result"]["protocolVersion"], NOVEMBER
                )

    def test_tools_list_returns_exactly_the_three_tools(self):
        """In 2025-11-25, tools/list returns exactly execute_python, execute_python_file and get_output_page, with no 2026-07-28 fields."""
        status, response = mcp_protocol.handle_request(
            november_request("tools/list"), NOVEMBER
        )
        self.assertEqual(status, 200)
        result = response["result"]
        self.assertEqual(
            sorted(tool["name"] for tool in result["tools"]),
            ["execute_python", "execute_python_file", "get_output_page"],
        )
        self.assertEqual(list(result), ["tools"])

    def test_ping_gets_an_empty_result(self):
        """In 2025-11-25, ping gets an empty result."""
        status, response = mcp_protocol.handle_request(
            november_request("ping", req_id="p"), NOVEMBER
        )
        self.assertEqual(status, 200)
        self.assertEqual(response, {"jsonrpc": "2.0", "id": "p", "result": {}})

    def test_an_unknown_method_gets_method_not_found(self):
        """In 2025-11-25, an unknown method, server/discover among them, gets a JSON-RPC method-not-found error."""
        for method in ("no/such/method", "server/discover"):
            with self.subTest(method=method):
                status, response = mcp_protocol.handle_request(
                    november_request(method, req_id=3), NOVEMBER
                )
                self.assertEqual(status, 200)
                self.assertEqual(response["id"], 3)
                self.assertEqual(response["error"]["code"], -32601)


class ToolResultTests(unittest.TestCase):
    def test_a_tool_result_is_one_json_text_block_marked_complete(self):
        """A 2026-07-28 tool result is one JSON text block, with resultType complete and no error flag."""
        response = mcp_protocol.tool_call_response(
            5,
            {"page": [], "has_more": False, "job_token": "t", "page_no": 0},
            VERSION,
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

    def test_a_2025_11_25_tool_result_is_one_json_text_block(self):
        """A 2025-11-25 tool result is one JSON text block alone, with no error flag."""
        response = mcp_protocol.tool_call_response(
            5,
            {"page": [], "has_more": False, "job_token": "t", "page_no": 0},
            NOVEMBER,
        )
        result = response["result"]
        self.assertEqual(list(result), ["content"])
        (block,) = result["content"]
        self.assertEqual(block["type"], "text")
        self.assertEqual(
            json.loads(block["text"]),
            {"page": [], "has_more": False, "page_no": 0, "job_token": "t"},
        )

    def test_a_failed_tool_call_is_flagged_is_error_and_keeps_its_payload(self):
        """A failed tool call is flagged isError in either version, and its payload still carries the error."""
        for version in BOTH_VERSIONS:
            with self.subTest(version=version):
                response = mcp_protocol.tool_call_response(
                    5,
                    {
                        "page": [],
                        "has_more": False,
                        "error": "page_no out of range",
                    },
                    version,
                )
                result = response["result"]
                self.assertIs(result["isError"], True)
                payload = json.loads(result["content"][0]["text"])
                self.assertEqual(payload["error"], "page_no out of range")

    def test_continuation_fields_come_before_the_page_at_the_same_length(self):
        """A tool result lists has_more, page_no, job_token and error before page, at the same length as page-first."""
        cases = [
            (
                {
                    "page": [{"stream": "stdout", "text": "hi\n"}],
                    "has_more": True,
                    "page_no": 0,
                    "job_token": "t",
                },
                ["has_more", "page_no", "job_token", "page"],
            ),
            (
                {"page": [], "has_more": True, "job_token": "t"},
                ["has_more", "job_token", "page"],
            ),
            (
                {
                    "page": [],
                    "has_more": False,
                    "error": "unknown or expired job_token",
                },
                ["has_more", "error", "page"],
            ),
            (
                {
                    "page": [],
                    "has_more": False,
                    "page_no": 1,
                    "job_token": "t",
                    "error": "page_no out of range",
                },
                ["has_more", "page_no", "job_token", "error", "page"],
            ),
        ]
        for result, order in cases:
            with self.subTest(order=order):
                payload = mcp_protocol.tool_result_payload(result)
                self.assertEqual(list(payload), order)
                former = {
                    "page": payload["page"],
                    "has_more": payload["has_more"],
                }
                for key in ("page_no", "job_token", "error"):
                    if key in payload:
                        former[key] = payload[key]
                self.assertEqual(
                    len(json.dumps(payload)), len(json.dumps(former))
                )


if __name__ == "__main__":
    unittest.main()
