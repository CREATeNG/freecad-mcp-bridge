"""Requirements on how an MCP client connects to the bridge and learns its tools.

Each test name, and the first line of its docstring, states one requirement
as an MCP client experiences it, against a real bridge in a real FreeCAD.
"""

import json
import os
import tempfile
import unittest

from tests.acceptance import mcp_client as mcp

SERVER_INFO = "io.modelcontextprotocol/serverInfo"
META_VERSION = "io.modelcontextprotocol/protocolVersion"
CLIENT_CAPABILITIES = "io.modelcontextprotocol/clientCapabilities"


def is_json(reply):
    return reply.headers["Content-Type"].startswith("application/json")


class ConnectingTests(unittest.TestCase):
    def test_the_bridge_offers_exactly_execute_python_execute_python_file_and_get_output_page(self):
        """The bridge offers exactly three tools: execute_python, execute_python_file and get_output_page."""
        reply = mcp.post(mcp.request("tools/list"))
        names = sorted(tool["name"] for tool in reply.json()["result"]["tools"])
        self.assertEqual(
            names, ["execute_python", "execute_python_file", "get_output_page"]
        )

    def test_discover_answers_as_plain_json_with_version_2026_07_28_the_tools_capability_and_server_info(self):
        """server/discover answers as plain JSON: 2026-07-28 as the one supported version, a tools capability, caching hints and the server's name and version."""
        reply = mcp.post(mcp.request("server/discover"))
        self.assertEqual(reply.status, 200)
        self.assertTrue(is_json(reply))
        result = reply.json()["result"]
        self.assertEqual(result["resultType"], "complete")
        self.assertEqual(result["supportedVersions"], ["2026-07-28"])
        self.assertIn("tools", result["capabilities"])
        self.assertGreaterEqual(result["ttlMs"], 0)
        self.assertIn(result["cacheScope"], ("public", "private"))
        server_info = result["_meta"][SERVER_INFO]
        self.assertEqual(server_info["name"], "freecad-mcp-bridge")
        self.assertTrue(server_info["version"])

    def test_tools_list_answers_as_plain_json_with_caching_hints(self):
        """tools/list answers as plain JSON, with resultType and caching hints."""
        reply = mcp.post(mcp.request("tools/list"))
        self.assertEqual(reply.status, 200)
        self.assertTrue(is_json(reply))
        result = reply.json()["result"]
        self.assertIn("tools", result)
        self.assertEqual(result["resultType"], "complete")
        self.assertGreaterEqual(result["ttlMs"], 0)
        self.assertIn(result["cacheScope"], ("public", "private"))

    def test_a_tool_call_answers_as_plain_json(self):
        """A tool call answers as plain JSON: one JSON-RPC response whose result is a text block marked complete."""
        message = mcp.request(
            "tools/call",
            {"name": "execute_python", "arguments": {"code": "print('json')"}},
        )
        reply = mcp.post(message)
        self.assertEqual(reply.status, 200)
        self.assertTrue(is_json(reply))
        envelope = reply.json()
        self.assertEqual(envelope["id"], message["id"])
        result = envelope["result"]
        self.assertEqual(result["resultType"], "complete")
        self.assertEqual(result["content"][0]["type"], "text")
        self.assertFalse(result.get("isError", False))
        mcp.follow(mcp.payload(result))

    def test_a_failed_tool_call_is_flagged_is_error_and_still_explains_itself(self):
        """A failed tool call (dead job token, page_no out of range, unreadable file) is flagged isError, and its payload still carries the error."""
        final = mcp.follow(mcp.execute("pass"))[-1]
        with tempfile.TemporaryDirectory() as folder:
            missing = os.path.join(folder, "missing.py")
            calls = {
                "dead job token": (
                    "get_output_page",
                    {"job_token": "0" * 32, "page_no": 0},
                ),
                "page_no out of range": (
                    "get_output_page",
                    {
                        "job_token": final["job_token"],
                        "page_no": final["page_no"] + 1,
                    },
                ),
                "unreadable file": (
                    "execute_python_file",
                    {"filepath": missing},
                ),
            }
            for case, (name, arguments) in calls.items():
                with self.subTest(case=case):
                    message = mcp.request(
                        "tools/call", {"name": name, "arguments": arguments}
                    )
                    result = mcp.tool_result(mcp.post(message), name)
                    self.assertIs(result.get("isError"), True)
                    self.assertTrue(mcp.payload(result)["error"])

    def test_a_python_traceback_is_ordinary_output_not_a_tool_error(self):
        """Code that raises is not a failed tool call: its traceback is ordinary output, with no isError."""
        message = mcp.request(
            "tools/call",
            {"name": "execute_python", "arguments": {"code": "1 / 0"}},
        )
        result = mcp.tool_result(mcp.post(message))
        self.assertFalse(result.get("isError", False))
        output = mcp.text(mcp.follow(mcp.payload(result)))
        self.assertIn("ZeroDivisionError", output)

    def test_a_notification_gets_202_with_an_empty_body(self):
        """A notification gets HTTP 202 with an empty body."""
        notification = {"jsonrpc": "2.0", "method": "notifications/cancelled"}
        reply = mcp.post(notification)
        self.assertEqual(reply.status, 202)
        self.assertEqual(reply.body, b"")

    def test_a_tool_call_with_no_id_gets_202_and_its_code_never_runs(self):
        """A tool call with no id is a notification: it gets HTTP 202 with an empty body, and its code never runs."""
        with tempfile.TemporaryDirectory() as folder:
            marker = os.path.join(folder, "ran")
            message = mcp.request(
                "tools/call",
                {
                    "name": "execute_python",
                    "arguments": {"code": f"open({marker!r}, 'w').close()"},
                },
            )
            del message["id"]
            reply = mcp.post(message)
            self.assertEqual(reply.status, 202)
            self.assertEqual(reply.body, b"")
            # Jobs run in submission order, so had it been queued, it would
            # have run before this one finishes.
            self.assertEqual(mcp.run("print('after')"), "after\n")
            self.assertFalse(os.path.exists(marker))

    def test_requests_need_no_session_and_the_server_issues_no_session_id(self):
        """Requests need no session: the server issues no Mcp-Session-Id, and every request works without one."""
        replies = [
            mcp.post(mcp.request("server/discover")),
            mcp.post(mcp.request("tools/list")),
            mcp.post(
                mcp.request(
                    "tools/call",
                    {"name": "execute_python", "arguments": {"code": "pass"}},
                )
            ),
        ]
        for reply in replies:
            self.assertEqual(reply.status, 200)
            self.assertIsNone(reply.headers.get("Mcp-Session-Id"))

    def test_listen_is_acknowledged_with_no_notifications_and_then_closed(self):
        """subscriptions/listen is acknowledged on an SSE stream with no notification types, then closed gracefully with nothing between."""
        message = mcp.request(
            "subscriptions/listen",
            {"notifications": {"toolsListChanged": True}},
        )
        reply = mcp.post(message)
        self.assertEqual(reply.status, 200)
        self.assertTrue(
            reply.headers["Content-Type"].startswith("text/event-stream")
        )
        acknowledged, closed = (json.loads(e) for e in reply.sse_events())
        subscription = "io.modelcontextprotocol/subscriptionId"
        self.assertEqual(
            acknowledged["method"], "notifications/subscriptions/acknowledged"
        )
        self.assertEqual(acknowledged["params"]["notifications"], {})
        self.assertEqual(
            acknowledged["params"]["_meta"][subscription], message["id"]
        )
        self.assertEqual(closed["id"], message["id"])
        self.assertEqual(closed["result"]["resultType"], "complete")
        self.assertEqual(
            closed["result"]["_meta"][subscription], message["id"]
        )


class ProtocolVersionTests(unittest.TestCase):
    def assertRejected(self, reply, status, code):
        self.assertEqual(reply.status, status)
        self.assertTrue(is_json(reply))
        error = reply.json()["error"]
        self.assertEqual(error["code"], code)
        return error

    def test_an_initialize_request_gets_400_and_the_unsupported_version_error_naming_2026_07_28(self):
        """An initialize request gets 400 and the unsupported-version error, naming 2026-07-28 as the one supported version."""
        message = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "legacy-client", "version": "0"},
            },
        }
        # As a handshake-era client sends it: no 2026-07-28 headers.
        reply = mcp.send(json.dumps(message).encode("utf-8"))
        error = self.assertRejected(reply, 400, -32022)
        self.assertEqual(error["data"]["supported"], ["2026-07-28"])
        self.assertEqual(error["data"]["requested"], "2025-11-25")

    def test_a_request_in_another_protocol_version_gets_400_and_the_unsupported_version_error(self):
        """A request in another protocol version gets 400 and the unsupported-version error, naming 2026-07-28."""
        message = mcp.request("tools/list")
        message["params"]["_meta"][META_VERSION] = "2025-11-25"
        reply = mcp.post(
            message, headers={"MCP-Protocol-Version": "2025-11-25"}
        )
        error = self.assertRejected(reply, 400, -32022)
        self.assertEqual(error["data"]["supported"], ["2026-07-28"])

    def test_a_request_without_the_protocol_version_header_gets_400_and_a_header_mismatch(self):
        """A request without the MCP-Protocol-Version header gets 400 and a header-mismatch error."""
        reply = mcp.post(
            mcp.request("tools/list"), headers={"MCP-Protocol-Version": None}
        )
        self.assertRejected(reply, 400, -32020)

    def test_a_version_header_differing_from_the_meta_version_gets_400_and_a_header_mismatch(self):
        """A request whose MCP-Protocol-Version header differs from its _meta version gets 400 and a header-mismatch error."""
        reply = mcp.post(
            mcp.request("tools/list"),
            headers={"MCP-Protocol-Version": "2025-11-25"},
        )
        self.assertRejected(reply, 400, -32020)

    def test_a_request_without_client_capabilities_gets_400_and_invalid_params(self):
        """A request whose _meta lacks the client capabilities gets 400 and an invalid-params error."""
        message = mcp.request("tools/list")
        del message["params"]["_meta"][CLIENT_CAPABILITIES]
        self.assertRejected(mcp.post(message), 400, -32602)

    def test_a_missing_or_differing_method_header_gets_400_and_a_header_mismatch(self):
        """A request whose Mcp-Method header is missing or differs from its method gets 400 and a header-mismatch error."""
        for value in (None, "server/discover"):
            with self.subTest(header=value):
                reply = mcp.post(
                    mcp.request("tools/list"), headers={"Mcp-Method": value}
                )
                self.assertRejected(reply, 400, -32020)

    def test_a_tool_call_whose_name_header_is_missing_or_differs_is_rejected_before_any_code_runs(self):
        """A tool call whose Mcp-Name header is missing or differs from the tool name gets 400 and a header-mismatch error, and its code never runs."""
        with tempfile.TemporaryDirectory() as folder:
            marker = os.path.join(folder, "ran")
            message = mcp.request(
                "tools/call",
                {
                    "name": "execute_python",
                    "arguments": {"code": f"open({marker!r}, 'w').close()"},
                },
            )
            for value in (None, "get_output_page"):
                with self.subTest(header=value):
                    reply = mcp.post(message, headers={"Mcp-Name": value})
                    self.assertRejected(reply, 400, -32020)
            # Jobs run in submission order, so had a rejected job been
            # queued, it would have run before this one finishes.
            self.assertEqual(mcp.run("print('after')"), "after\n")
            self.assertFalse(os.path.exists(marker))

    def test_an_unknown_method_such_as_ping_gets_404_and_method_not_found(self):
        """An unknown method, such as ping, gets 404 and a JSON-RPC method-not-found error."""
        reply = mcp.post(mcp.request("ping"))
        self.assertRejected(reply, 404, -32601)


if __name__ == "__main__":
    unittest.main()
