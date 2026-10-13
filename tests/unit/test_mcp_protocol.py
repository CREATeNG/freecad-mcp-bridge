"""Requirements on the MCP lifecycle methods (mcp_protocol.py).

Each test name, and the first line of its docstring, states one requirement
as an MCP client connecting to the bridge experiences it.
"""

import re
import unittest
from pathlib import Path

from freecad.mcp_bridge import mcp_protocol
from freecad.mcp_bridge.constants import PROTOCOL_VERSION

ROOT = Path(__file__).resolve().parents[2]


def package_version():
    text = (ROOT / "package.xml").read_text(encoding="utf-8")
    return re.search(r"<version>([^<]+)</version>", text).group(1)


class LifecycleTests(unittest.TestCase):
    def test_initialize_returns_protocol_version_capabilities_and_server_info(self):
        """initialize returns the declared protocol version, the tools capability and the server's name and version."""
        response = mcp_protocol.handle_request(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        )
        self.assertEqual(response["id"], 1)
        result = response["result"]
        self.assertEqual(result["protocolVersion"], PROTOCOL_VERSION)
        self.assertIn("tools", result["capabilities"])
        self.assertEqual(
            result["serverInfo"],
            {"name": "freecad-mcp-bridge", "version": package_version()},
        )

    def test_tools_list_returns_exactly_the_three_tools(self):
        """tools/list returns exactly the three tools execute_python, execute_python_file and get_output_page."""
        response = mcp_protocol.handle_request(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        )
        names = [tool["name"] for tool in response["result"]["tools"]]
        self.assertEqual(
            sorted(names),
            ["execute_python", "execute_python_file", "get_output_page"],
        )

    def test_a_notification_gets_no_response(self):
        """A notification (no id) such as notifications/initialized gets no response."""
        response = mcp_protocol.handle_request(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}
        )
        self.assertIsNone(response)

    def test_an_unknown_method_gets_method_not_found(self):
        """An unknown method gets a JSON-RPC method-not-found error."""
        response = mcp_protocol.handle_request(
            {"jsonrpc": "2.0", "id": 3, "method": "no/such/method"}
        )
        self.assertEqual(response["id"], 3)
        self.assertEqual(response["error"]["code"], -32601)
        self.assertNotIn("result", response)


if __name__ == "__main__":
    unittest.main()
