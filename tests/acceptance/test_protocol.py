"""Requirements on how an MCP client connects to the bridge and learns its tools.

Each test name, and the first line of its docstring, states one requirement
as an MCP client experiences it, against a real bridge in a real FreeCAD.
"""

import json
import unittest

from tests.acceptance import mcp_client as mcp


class ConnectingTests(unittest.TestCase):
    def test_the_bridge_offers_exactly_execute_python_execute_python_file_and_get_output_page(self):
        """The bridge offers exactly three tools: execute_python, execute_python_file and get_output_page."""
        reply = mcp.post(mcp.request("tools/list"))
        names = sorted(tool["name"] for tool in reply.json()["result"]["tools"])
        self.assertEqual(
            names, ["execute_python", "execute_python_file", "get_output_page"]
        )

    def test_initialize_answers_with_protocol_version_tools_capability_and_server_info_as_plain_json(self):
        """initialize answers as plain JSON with protocol version 2025-03-26, a tools capability and the server's name and version."""
        reply = mcp.post(mcp.request("initialize", mcp.INITIALIZE_PARAMS))
        self.assertEqual(reply.status, 200)
        self.assertTrue(reply.headers["Content-Type"].startswith("application/json"))
        result = reply.json()["result"]
        self.assertEqual(result["protocolVersion"], "2025-03-26")
        self.assertIn("tools", result["capabilities"])
        self.assertEqual(result["serverInfo"]["name"], "freecad-mcp-bridge")
        self.assertTrue(result["serverInfo"]["version"])

    def test_tools_list_answers_as_plain_json(self):
        """tools/list answers as plain JSON."""
        reply = mcp.post(mcp.request("tools/list"))
        self.assertEqual(reply.status, 200)
        self.assertTrue(reply.headers["Content-Type"].startswith("application/json"))
        self.assertIn("tools", reply.json()["result"])

    def test_a_tool_call_answers_as_a_single_event_sse_stream(self):
        """A tool call answers as an SSE stream carrying one event, whose data is the JSON-RPC response."""
        message = mcp.request(
            "tools/call",
            {"name": "execute_python", "arguments": {"code": "print('sse')"}},
        )
        reply = mcp.post(message)
        self.assertEqual(reply.status, 200)
        self.assertTrue(reply.headers["Content-Type"].startswith("text/event-stream"))
        events = reply.sse_events()
        self.assertEqual(len(events), 1)
        envelope = json.loads(events[0])
        self.assertEqual(envelope["id"], message["id"])
        self.assertEqual(envelope["result"]["content"][0]["type"], "text")

    def test_a_notification_gets_202_with_an_empty_body(self):
        """A notification such as notifications/initialized gets HTTP 202 with an empty body."""
        reply = mcp.post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual(reply.status, 202)
        self.assertEqual(reply.body, b"")

    def test_requests_need_no_session_and_the_server_issues_no_session_id(self):
        """Requests need no session: the server issues no Mcp-Session-Id, and every request works without one."""
        replies = [
            mcp.post(mcp.request("initialize", mcp.INITIALIZE_PARAMS)),
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


if __name__ == "__main__":
    unittest.main()
