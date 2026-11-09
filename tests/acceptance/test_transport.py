"""Requirements on the bridge's HTTP endpoint and who may reach it.

Each test name, and the first line of its docstring, states one requirement
as an MCP client (or another program on the machine) experiences it, against
a real bridge in a real FreeCAD.
"""

import http.client
import json
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
    def test_get_or_delete_on_the_endpoint_returns_405(self):
        """GET or DELETE on /mcp returns 405: the server offers no standalone stream and no session to end."""
        for method in ("GET", "DELETE"):
            with self.subTest(method=method):
                self.assertEqual(mcp.send(method=method).status, 405)

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
    def test_a_request_carrying_an_origin_header_is_rejected_before_any_code_runs(self):
        """A request carrying any Origin header is rejected with 403, and its code never runs."""
        with tempfile.TemporaryDirectory() as folder:
            marker = os.path.join(folder, "ran")
            message = mcp.request(
                "tools/call",
                {
                    "name": "execute_python",
                    "arguments": {"code": f"open({marker!r}, 'w').close()"},
                },
            )
            for origin in (
                "http://evil.example",
                f"http://localhost:{PORT}",
                f"http://127.0.0.1:{PORT}",
                "",
            ):
                with self.subTest(origin=origin):
                    reply = mcp.post(message, headers={"Origin": origin})
                    self.assertEqual(reply.status, 403)
            # Jobs run in submission order, so had a rejected job been
            # queued, it would have run before this one finishes.
            self.assertEqual(mcp.run("print('after')"), "after\n")
            self.assertFalse(os.path.exists(marker))

    def test_a_request_with_no_origin_header_is_accepted(self):
        """A request with no Origin header is accepted."""
        reply = mcp.post(mcp.request("tools/list"))
        self.assertEqual(reply.status, 200)


class HostTests(unittest.TestCase):
    def _post(self, host_header):
        token = mcp._token_from_file()
        message = mcp.request("tools/list")
        body = json.dumps(message).encode("utf-8")
        headers = {
            "Host": host_header,
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            **mcp.standard_headers(message),
        }
        connection = http.client.HTTPConnection(
            "127.0.0.1", PORT, timeout=10
        )
        try:
            connection.request("POST", "/mcp", body=body, headers=headers)
            response = connection.getresponse()
            response.read()
            return response.status
        finally:
            connection.close()

    def test_the_host_header_must_name_the_bound_address(self):
        """A Host other than the bound address is refused; loopback accepts 127.0.0.1, localhost and [::1]."""
        self.assertEqual(self._post("evil.example"), 403)
        self.assertEqual(self._post(f"127.0.0.1:{PORT}"), 200)
        self.assertEqual(self._post("localhost"), 200)
        self.assertEqual(self._post("[::1]"), 200)


class LoopbackTests(unittest.TestCase):
    def test_the_bridge_is_reachable_only_on_the_loopback_address(self):
        """By default, the bridge listens on 127.0.0.1 only, not on the LAN."""
        address = non_loopback_address()
        if address is None:
            self.skipTest("this machine has no non-loopback IPv4 address")
        socket.create_connection(("127.0.0.1", PORT), timeout=5).close()
        with self.assertRaises(OSError):
            socket.create_connection((address, PORT), timeout=5).close()


if __name__ == "__main__":
    unittest.main()
