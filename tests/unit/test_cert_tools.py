"""A certificate tool makes a pair Python's ssl will serve and trust."""

import socket
import ssl
import stat
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from freecad.mcp_bridge import cert_tools


def _handshake(cert: Path, key: Path) -> str:
    """Serve one handshake and verify it against `cert` alone."""
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(str(cert), str(key))
    client_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client_ctx.check_hostname = False
    client_ctx.verify_mode = ssl.CERT_REQUIRED
    client_ctx.load_verify_locations(str(cert))
    raw = socket.socket()
    raw.bind(("127.0.0.1", 0))
    raw.listen(1)
    port = raw.getsockname()[1]
    serving = server_ctx.wrap_socket(raw, server_side=True)

    def accept():
        try:
            conn, _addr = serving.accept()
        except OSError:
            return
        try:
            conn.recv(1)
        finally:
            conn.close()

    thread = threading.Thread(target=accept)
    thread.start()
    client_raw = socket.create_connection(("127.0.0.1", port))
    client = client_ctx.wrap_socket(client_raw)
    version = client.version()
    client.send(b"x")
    client.close()
    thread.join(2)
    serving.close()
    return version or ""


class CertToolTests(unittest.TestCase):
    def test_each_tool_here_makes_a_pair_python_verifies(self):
        """Each tool on this machine makes RSA and ECDSA pairs ssl loads."""
        tools = cert_tools.find_tools()
        if not tools:
            self.skipTest("no certificate tool on this machine")
        hosts = ["freecad.local", "192.0.2.10"]
        for tool in tools:
            for key_type in (cert_tools.KEY_RSA, cert_tools.KEY_ECDSA):
                with self.subTest(tool=tool["id"], key_type=key_type):
                    self._check_one(tool["id"], key_type, hosts)

    def _check_one(self, tool, key_type, hosts):
        with tempfile.TemporaryDirectory() as folder:
            cert = Path(folder) / "cert.pem"
            key = Path(folder) / "key.pem"
            cert_tools.generate(tool, key_type, hosts, str(cert), str(key))
            self.assertTrue(cert.is_file())
            self.assertTrue(key.is_file())
            if not sys.platform.startswith("win"):
                mode = stat.S_IMODE(key.stat().st_mode)
                self.assertEqual(mode & 0o077, 0, oct(mode))
            version = _handshake(cert, key)
            self.assertTrue(version)


if __name__ == "__main__":
    unittest.main()
