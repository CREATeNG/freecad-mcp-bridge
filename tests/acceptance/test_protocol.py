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
NOVEMBER = mcp.NOVEMBER_VERSION
BOTH_VERSIONS = ["2026-07-28", "2025-11-25"]


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

    def test_discover_answers_as_plain_json_with_both_versions_the_tools_capability_and_server_info(self):
        """server/discover answers as plain JSON: 2026-07-28 and 2025-11-25 as the supported versions, a tools capability, caching hints and the server's name and version."""
        reply = mcp.post(mcp.request("server/discover"))
        self.assertEqual(reply.status, 200)
        self.assertTrue(is_json(reply))
        result = reply.json()["result"]
        self.assertEqual(result["resultType"], "complete")
        self.assertEqual(result["supportedVersions"], BOTH_VERSIONS)
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


class NovemberTests(unittest.TestCase):
    """Requirements on a client speaking MCP 2025-11-25."""

    def test_initialize_answers_as_plain_json_with_version_2025_11_25_the_tools_capability_and_server_info(self):
        """initialize answers as plain JSON: protocol version 2025-11-25, a tools capability and the server's name and version, with no session id."""
        reply = mcp.initialize()
        self.assertEqual(reply.status, 200)
        self.assertTrue(is_json(reply))
        self.assertIsNone(reply.headers.get("Mcp-Session-Id"))
        result = reply.json()["result"]
        self.assertEqual(result["protocolVersion"], NOVEMBER)
        self.assertIn("tools", result["capabilities"])
        self.assertEqual(result["serverInfo"]["name"], "freecad-mcp-bridge")
        self.assertTrue(result["serverInfo"]["version"])

    def test_an_initialize_asking_for_an_older_version_gets_2025_11_25(self):
        """An initialize asking for an older version gets 2025-11-25, the version the server offers instead."""
        for requested in ("2025-06-18", "2025-03-26"):
            with self.subTest(requested=requested):
                reply = mcp.initialize(requested)
                self.assertEqual(reply.status, 200)
                self.assertEqual(
                    reply.json()["result"]["protocolVersion"], NOVEMBER
                )

    def test_after_the_handshake_the_initialized_notification_gets_202_and_tools_list_answers(self):
        """After initialize, the initialized notification gets 202, and tools/list with the 2025-11-25 header returns exactly the three tools."""
        self.assertEqual(mcp.initialize().status, 200)
        initialized = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        reply = mcp.post(initialized, version=NOVEMBER)
        self.assertEqual(reply.status, 202)
        self.assertEqual(reply.body, b"")
        reply = mcp.post(
            mcp.request("tools/list", version=NOVEMBER), version=NOVEMBER
        )
        self.assertEqual(reply.status, 200)
        self.assertTrue(is_json(reply))
        tools = reply.json()["result"]["tools"]
        names = sorted(tool["name"] for tool in tools)
        self.assertEqual(
            names, ["execute_python", "execute_python_file", "get_output_page"]
        )

    def test_ping_gets_an_empty_result(self):
        """ping with the 2025-11-25 header gets an empty result."""
        message = mcp.request("ping", version=NOVEMBER)
        reply = mcp.post(message, version=NOVEMBER)
        self.assertEqual(reply.status, 200)
        self.assertEqual(
            reply.json(), {"jsonrpc": "2.0", "id": message["id"], "result": {}}
        )

    def test_a_tool_call_answers_as_plain_json_and_runs_to_completion(self):
        """A tool call with the 2025-11-25 header answers as plain JSON, and its output follows to completion."""
        message = mcp.request(
            "tools/call",
            {"name": "execute_python", "arguments": {"code": "print('nov')"}},
            version=NOVEMBER,
        )
        reply = mcp.post(message, version=NOVEMBER)
        self.assertEqual(reply.status, 200)
        self.assertTrue(is_json(reply))
        envelope = reply.json()
        self.assertEqual(envelope["id"], message["id"])
        result = envelope["result"]
        self.assertEqual(result["content"][0]["type"], "text")
        self.assertFalse(result.get("isError", False))
        results = mcp.follow(mcp.payload(result), version=NOVEMBER)
        self.assertEqual(mcp.text(results), "nov\n")

    def test_a_failed_tool_call_is_flagged_is_error_and_still_explains_itself(self):
        """A failed 2025-11-25 tool call (dead job token, unreadable file) is flagged isError, and its payload still carries the error."""
        with tempfile.TemporaryDirectory() as folder:
            calls = {
                "dead job token": (
                    "get_output_page",
                    {"job_token": "0" * 32, "page_no": 0},
                ),
                "unreadable file": (
                    "execute_python_file",
                    {"filepath": os.path.join(folder, "missing.py")},
                ),
            }
            for case, (name, arguments) in calls.items():
                with self.subTest(case=case):
                    message = mcp.request(
                        "tools/call",
                        {"name": name, "arguments": arguments},
                        version=NOVEMBER,
                    )
                    reply = mcp.post(message, version=NOVEMBER)
                    result = mcp.tool_result(reply, name)
                    self.assertIs(result.get("isError"), True)
                    self.assertTrue(mcp.payload(result)["error"])

    def test_a_python_traceback_is_ordinary_output_not_a_tool_error(self):
        """In 2025-11-25 too, code that raises is not a failed tool call: its traceback is ordinary output, with no isError."""
        message = mcp.request(
            "tools/call",
            {"name": "execute_python", "arguments": {"code": "1 / 0"}},
            version=NOVEMBER,
        )
        result = mcp.tool_result(mcp.post(message, version=NOVEMBER))
        self.assertFalse(result.get("isError", False))
        results = mcp.follow(mcp.payload(result), version=NOVEMBER)
        self.assertIn("ZeroDivisionError", mcp.text(results))

    def test_a_request_without_the_protocol_version_header_gets_400(self):
        """A 2025-11-25 request other than initialize without the MCP-Protocol-Version header gets 400, and a tool call's code never runs."""
        with tempfile.TemporaryDirectory() as folder:
            marker = os.path.join(folder, "ran")
            messages = (
                mcp.request("tools/list", version=NOVEMBER),
                mcp.request("ping", version=NOVEMBER),
                mcp.request(
                    "tools/call",
                    {
                        "name": "execute_python",
                        "arguments": {
                            "code": f"open({marker!r}, 'w').close()"
                        },
                    },
                    version=NOVEMBER,
                ),
            )
            for message in messages:
                with self.subTest(method=message["method"]):
                    reply = mcp.send(json.dumps(message).encode("utf-8"))
                    self.assertEqual(reply.status, 400)
                    self.assertTrue(is_json(reply))
                    self.assertEqual(reply.json()["error"]["code"], -32020)
            # Jobs run in submission order, so had a rejected job been
            # queued, it would have run before this one finishes.
            self.assertEqual(mcp.run("print('after')"), "after\n")
            self.assertFalse(os.path.exists(marker))


class ProtocolVersionTests(unittest.TestCase):
    def assertRejected(self, reply, status, code):
        self.assertEqual(reply.status, status)
        self.assertTrue(is_json(reply))
        error = reply.json()["error"]
        self.assertEqual(error["code"], code)
        return error

    def test_a_request_in_another_protocol_version_gets_400_and_the_unsupported_version_error_naming_both(self):
        """A request in another protocol version gets 400 and the unsupported-version error, naming 2026-07-28 and 2025-11-25."""
        with self.subTest(form="2026-07-28"):
            message = mcp.request("tools/list")
            message["params"]["_meta"][META_VERSION] = "2025-06-18"
            reply = mcp.post(
                message, headers={"MCP-Protocol-Version": "2025-06-18"}
            )
            error = self.assertRejected(reply, 400, -32022)
            self.assertEqual(error["data"]["supported"], BOTH_VERSIONS)
            self.assertEqual(error["data"]["requested"], "2025-06-18")
        with self.subTest(form="2025-11-25"):
            reply = mcp.post(
                mcp.request("tools/list", version=NOVEMBER),
                version="2025-06-18",
            )
            error = self.assertRejected(reply, 400, -32022)
            self.assertEqual(error["data"]["supported"], BOTH_VERSIONS)

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
        """In 2026-07-28, an unknown method, such as ping, gets 404 and a JSON-RPC method-not-found error."""
        reply = mcp.post(mcp.request("ping"))
        self.assertRejected(reply, 404, -32601)


if __name__ == "__main__":
    unittest.main()
