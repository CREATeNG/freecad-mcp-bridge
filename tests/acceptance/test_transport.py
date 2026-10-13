"""Requirements on the bridge's HTTP endpoint and who may reach it.

Each test name, and the first line of its docstring, states one requirement
as an MCP client (or another program on the machine) experiences it, against
a real bridge in a real FreeCAD.
"""

import os
import socket
import tempfile
import unittest
from urllib.parse import urlsplit

from tests.acceptance import mcp_client as mcp

PORT = urlsplit(mcp.BRIDGE_URL).port


def non_loopback_address():
    """This machine's outward-facing IPv4 address, or None if it has none."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            # A UDP connect only picks a route; it sends nothing.
            probe.connect(("192.0.2.1", 9))
        except OSError:
            return None
        address = probe.getsockname()[0]
    if address.startswith("127.") or address == "0.0.0.0":
        return None
    return address


class EndpointTests(unittest.TestCase):
    def test_get_on_the_endpoint_returns_405(self):
        """GET on /mcp returns 405: the server offers no server-initiated stream."""
        self.assertEqual(mcp.send(method="GET").status, 405)

    def test_a_request_to_another_path_returns_404(self):
        """A request to any path other than /mcp returns 404."""
        reply = mcp.post(mcp.request("tools/list"), url=mcp.url_for("/elsewhere"))
        self.assertEqual(reply.status, 404)

    def test_malformed_json_returns_400_with_a_parse_error(self):
        """A request body that is not valid JSON returns 400 with a JSON-RPC parse error."""
        reply = mcp.send(b"{not json")
        self.assertEqual(reply.status, 400)
        self.assertEqual(reply.json()["error"]["code"], -32700)


class OriginTests(unittest.TestCase):
    def test_a_request_from_a_non_loopback_origin_is_rejected_before_any_code_runs(self):
        """A request carrying a non-loopback Origin is rejected with 403, and its code never runs."""
        with tempfile.TemporaryDirectory() as folder:
            marker = os.path.join(folder, "ran")
            message = mcp.request(
                "tools/call",
                {
                    "name": "execute_python",
                    "arguments": {"code": f"open({marker!r}, 'w').close()"},
                },
            )
            reply = mcp.post(message, headers={"Origin": "http://evil.example"})
            self.assertEqual(reply.status, 403)
            # Jobs run in submission order, so had the rejected job been
            # queued, it would have run before this one finishes.
            self.assertEqual(mcp.run("print('after')"), "after\n")
            self.assertFalse(os.path.exists(marker))

    def test_requests_with_no_origin_or_a_loopback_origin_are_accepted(self):
        """Requests with no Origin, or an Origin of localhost or 127.0.0.1, are accepted."""
        for origin in (
            None,
            f"http://localhost:{PORT}",
            f"http://127.0.0.1:{PORT}",
        ):
            with self.subTest(origin=origin):
                headers = {"Origin": origin} if origin else None
                reply = mcp.post(mcp.request("tools/list"), headers=headers)
                self.assertEqual(reply.status, 200)


class LoopbackTests(unittest.TestCase):
    def test_the_bridge_is_reachable_only_on_the_loopback_address(self):
        """The bridge listens on 127.0.0.1 only: it cannot be reached on the machine's network address."""
        address = non_loopback_address()
        if address is None:
            self.skipTest("this machine has no non-loopback IPv4 address")
        socket.create_connection(("127.0.0.1", PORT), timeout=5).close()
        with self.assertRaises(OSError):
            socket.create_connection((address, PORT), timeout=5).close()


if __name__ == "__main__":
    unittest.main()
