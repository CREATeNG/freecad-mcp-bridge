"""A certificate tool makes a pair Python's ssl will serve and trust."""

import ctypes
import socket
import ssl
import subprocess
import stat
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

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

    def test_a_failing_tool_leaves_an_existing_pair_byte_for_byte(self):
        """A tool that fails leaves the certificate and key already there."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cert = root / "cert.pem"
            key = root / "key.pem"
            cert.write_bytes(b"cert-bytes")
            key.write_bytes(b"key-bytes")
            with (
                mock.patch(
                    "freecad.mcp_bridge.cert_tools.shutil.which",
                    return_value="openssl",
                ),
                mock.patch.object(
                    cert_tools,
                    "_run",
                    side_effect=cert_tools.CertToolError("boom"),
                ),
            ):
                with self.assertRaises(cert_tools.CertToolError):
                    cert_tools.generate(
                        "openssl",
                        cert_tools.KEY_RSA,
                        ["freecad.local"],
                        str(cert),
                        str(key),
                    )
            self.assertEqual(cert.read_bytes(), b"cert-bytes")
            self.assertEqual(key.read_bytes(), b"key-bytes")
            leftovers = [
                path.name
                for path in root.iterdir()
                if path.name not in ("cert.pem", "key.pem")
            ]
            self.assertEqual(leftovers, [])


class RunWindowTests(unittest.TestCase):
    def test_run_passes_the_no_window_flag_on_windows(self):
        """On Windows, _run starts the tool with no console window."""
        if not sys.platform.startswith("win"):
            self.skipTest("the console flag is Windows only")
        with mock.patch(
            "freecad.mcp_bridge.cert_tools.subprocess.run"
        ) as run:
            run.return_value = subprocess.CompletedProcess([], 0)
            cert_tools._run(["openssl", "version"])
        self.assertEqual(
            run.call_args.kwargs.get("creationflags"),
            subprocess.CREATE_NO_WINDOW,
        )


class ValidDaysTests(unittest.TestCase):
    """Generate self-signed certificate's Valid for: 825 days unless
    chosen, 1 to 3650, reaching each tool's command."""

    def _commands(self, tool, key_type, **kwargs):
        """Run generate with `_run` caught; return the commands it ran."""
        commands = []
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with (
                mock.patch(
                    "freecad.mcp_bridge.cert_tools.shutil.which",
                    return_value="openssl",
                ),
                mock.patch.object(
                    cert_tools,
                    "_powershell_exe",
                    return_value=Path("powershell.exe"),
                ),
                mock.patch.object(
                    cert_tools, "_run", side_effect=commands.append
                ),
            ):
                cert_tools.generate(
                    tool,
                    key_type,
                    ["127.0.0.1"],
                    str(root / "cert.pem"),
                    str(root / "key.pem"),
                    **kwargs,
                )
        return commands

    def test_the_default_is_825_days(self):
        """Without a choice, the certificate is valid for 825 days."""
        from freecad.mcp_bridge.constants import (
            CERT_VALID_DAYS_DEFAULT,
            CERT_VALID_DAYS_MAX,
            CERT_VALID_DAYS_MIN,
        )

        self.assertEqual(CERT_VALID_DAYS_DEFAULT, 825)
        self.assertEqual((CERT_VALID_DAYS_MIN, CERT_VALID_DAYS_MAX), (1, 3650))
        command = self._commands("openssl", cert_tools.KEY_RSA)[-1]
        self.assertEqual(command[command.index("-days") + 1], "825")

    def test_openssl_gets_the_chosen_days(self):
        """openssl's -days is the chosen number, for both key types."""
        for key_type in (cert_tools.KEY_RSA, cert_tools.KEY_ECDSA):
            with self.subTest(key_type=key_type):
                command = self._commands("openssl", key_type, days=30)[-1]
                self.assertEqual(command[command.index("-days") + 1], "30")

    def test_powershell_gets_the_chosen_days(self):
        """The PowerShell script is given the chosen number as -ValidDays."""
        command = self._commands(
            "powershell", cert_tools.KEY_ECDSA, days=3650
        )[-1]
        self.assertEqual(command[command.index("-ValidDays") + 1], "3650")

    def test_days_outside_1_to_3650_are_refused_before_any_tool_runs(self):
        """0, 3651 and a value that isn't a whole number are refused, and
        no tool runs; 1 and 3650 are taken."""
        for days in (0, 3651, -5, True, "30", 30.0):
            with self.subTest(days=days):
                with self.assertRaises(cert_tools.CertToolError) as caught:
                    self._commands("openssl", cert_tools.KEY_RSA, days=days)
                self.assertEqual(
                    str(caught.exception), "Valid for must be 1 to 3650 days."
                )
        for days in (1, 3650):
            with self.subTest(days=days):
                command = self._commands(
                    "openssl", cert_tools.KEY_RSA, days=days
                )[-1]
                self.assertEqual(
                    command[command.index("-days") + 1], str(days)
                )

    def test_each_tool_here_makes_a_certificate_valid_for_the_days(self):
        """Each tool on this machine makes a certificate that expires the
        chosen number of days from now."""
        import datetime

        tools = cert_tools.find_tools()
        if not tools:
            self.skipTest("no certificate tool on this machine")
        today = datetime.datetime.now(datetime.timezone.utc).date()
        for tool in tools:
            with self.subTest(tool=tool["id"]):
                with tempfile.TemporaryDirectory() as folder:
                    cert = Path(folder) / "cert.pem"
                    cert_tools.generate(
                        tool["id"],
                        cert_tools.KEY_ECDSA,
                        ["127.0.0.1"],
                        str(cert),
                        str(Path(folder) / "key.pem"),
                        days=30,
                    )
                    decoded = ssl._ssl._test_decode_cert(str(cert))
                expiry = datetime.datetime.fromtimestamp(
                    ssl.cert_time_to_seconds(decoded["notAfter"]),
                    datetime.timezone.utc,
                ).date()
                self.assertIn((expiry - today).days, (30, 31))


if __name__ == "__main__":
    unittest.main()
