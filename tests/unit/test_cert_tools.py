"""A certificate tool makes a pair Python's ssl will serve and trust."""

import ctypes
import socket
import ssl
import stat
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from freecad.mcp_bridge import cert_tools


def _windows_ace_count(path):
    """How many allow entries the file's DACL has, on Windows."""
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class AclSizeInformation(ctypes.Structure):
        _fields_ = [
            ("AceCount", wintypes.DWORD),
            ("AclBytesInUse", wintypes.DWORD),
            ("AclBytesFree", wintypes.DWORD),
        ]

    advapi32.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi32.GetAclInformation.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    advapi32.GetAclInformation.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    dacl = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    result = advapi32.GetNamedSecurityInfoW(
        str(path),
        1,
        0x4,
        None,
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(descriptor),
    )
    if result != 0:
        raise ctypes.WinError(result)
    try:
        info = AclSizeInformation()
        if not advapi32.GetAclInformation(
            dacl, ctypes.byref(info), ctypes.sizeof(info), 2
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return info.AceCount
    finally:
        kernel32.LocalFree(descriptor)


def _handshake(cert: Path, key: Path, server_hostname: str) -> str:
    """Serve one handshake and verify `server_hostname` against `cert`."""
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(str(cert), str(key))
    client_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client_ctx.check_hostname = True
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
        except OSError:
            return
        finally:
            conn.close()

    thread = threading.Thread(target=accept)
    thread.start()
    client_raw = socket.create_connection(("127.0.0.1", port))
    client = client_ctx.wrap_socket(
        client_raw, server_hostname=server_hostname
    )
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
            root = Path(folder)
            parent_mode = stat.S_IMODE(root.stat().st_mode)
            cert = root / "made" / "cert.pem"
            key = root / "made" / "key.pem"
            cert_tools.generate(tool, key_type, hosts, str(cert), str(key))
            self.assertTrue(cert.is_file())
            self.assertTrue(key.is_file())
            # The directory that already existed keeps its access.
            self.assertEqual(
                stat.S_IMODE(root.stat().st_mode), parent_mode
            )
            made_mode = stat.S_IMODE(cert.parent.stat().st_mode)
            if sys.platform.startswith("win"):
                # The tool overwrote the key and kept its access list.
                self.assertEqual(_windows_ace_count(key), 1)
                self.assertEqual(_windows_ace_count(cert.parent), 1)
            else:
                self.assertEqual(made_mode & 0o077, 0, oct(made_mode))
                mode = stat.S_IMODE(key.stat().st_mode)
                self.assertEqual(mode & 0o077, 0, oct(mode))
            for name in hosts:
                version = _handshake(cert, key, name)
                self.assertTrue(version)


if __name__ == "__main__":
    unittest.main()
