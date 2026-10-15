"""The packed shim, called over stdio the way a client calls it.

Runs only when BUNDLE_MCPB names a freecad-mcp-bridge.mcpb. The
bridge port comes from ACCEPTANCE_READY_FILE, and the server
command comes from the bundle's own manifest.
"""

import json
import os
import queue
import subprocess
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path

MODERN = "2026-07-28"
LEGACY = "2025-11-25"
LINE_COUNT = 3000
CALL_TIMEOUT_S = 60
EXIT_TIMEOUT_S = 3
_META_VERSION = "io.modelcontextprotocol/protocolVersion"
_META_INFO = "io.modelcontextprotocol/clientInfo"
_META_CAPS = "io.modelcontextprotocol/clientCapabilities"


def _distinct_lines():
    """3000 lines, together larger than a 64 KiB page."""
    mark = "x" * 60
    return [f"{i:04d}-{mark}" for i in range(LINE_COUNT)]


def _fail(message):
    raise AssertionError(message) from None


def _unpack(bundle, dest):
    try:
        with zipfile.ZipFile(bundle) as archive:
            archive.extractall(dest)
    except (OSError, zipfile.BadZipFile) as err:
        _fail(
            "FAIL: freecad-mcp-bridge.mcpb is missing or not a "
            f"valid zip archive: {err}"
        )
    return dest


def _entry_point(root):
    path = root / "manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        config = manifest["server"]["mcp_config"]
        command = config["command"]
        args = config["args"]
        if not isinstance(command, str) or not command:
            raise KeyError(command)
        if not isinstance(args, list) or not args:
            raise KeyError("args")
        resolved = []
        for arg in args:
            if not isinstance(arg, str):
                raise KeyError(arg)
            resolved.append(arg.replace("${__dirname}", str(root)))
        if not (root / "index.js").is_file():
            raise KeyError("index.js")
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        _fail(
            "FAIL: manifest.json missing or invalid entry point: "
            f"{path}"
        )
    return command, resolved


def _bridge_port():
    path = os.environ.get("ACCEPTANCE_READY_FILE", "")
    try:
        port = Path(path).read_text(encoding="utf-8").strip()
    except OSError as err:
        _fail(f"ACCEPTANCE_READY_FILE has no port ({path}): {err}")
    if not port:
        _fail(f"ACCEPTANCE_READY_FILE has no port ({path})")
    return port


class _Shim:
    """One stdio server, with its stdout lines queued for the client."""

    def __init__(self, command, args, port, cwd):
        env = os.environ.copy()
        env["FREECAD_MCP_PORT"] = port
        try:
            self.proc = subprocess.Popen(
                [command, *args],
                cwd=str(cwd),
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as err:
            _fail(
                "FAIL: shim process exited prematurely with code "
                f"not-started: {err}"
            )
        self.lines = queue.Queue()
        self._stderr = []
        self._lock = threading.Lock()
        threading.Thread(
            target=self._read_stdout, daemon=True
        ).start()
        self._stderr_thread = threading.Thread(
            target=self._read_stderr, daemon=True
        )
        self._stderr_thread.start()
        self._next_id = 1

    def _read_stdout(self):
        for line in iter(self.proc.stdout.readline, b""):
            self.lines.put(line)
        self.lines.put(None)

    def _read_stderr(self):
        data = self.proc.stderr.read()
        with self._lock:
            self._stderr.append(data)

    def stderr_text(self):
        self._stderr_thread.join(timeout=1)
        with self._lock:
            blob = b"".join(self._stderr)
        return blob.decode("utf-8", "replace").strip()

    def send(self, message):
        raw = json.dumps(message).encode("utf-8") + b"\n"
        self.proc.stdin.write(raw)
        self.proc.stdin.flush()

    def response(self, timeout):
        """The next JSON-RPC line, or None when `timeout` passes."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            code = self.proc.poll()
            if code is not None:
                _fail(
                    "FAIL: shim process exited prematurely with code "
                    f"{code}: {self.stderr_text()}"
                )
            try:
                line = self.lines.get(timeout=0.2)
            except queue.Empty:
                continue
            if line is None:
                code = self.proc.poll()
                _fail(
                    "FAIL: shim process exited prematurely with code "
                    f"{code}: {self.stderr_text()}"
                )
            return json.loads(line.decode("utf-8"))
        return None

    def close_stdin(self):
        self.proc.stdin.close()
        try:
            code = self.proc.wait(timeout=EXIT_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            _fail(
                "FAIL: shim hung after stdin closed, "
                "process did not exit within 3s"
            )
        if code != 0:
            _fail(
                "FAIL: shim process exited prematurely with code "
                f"{code}: {self.stderr_text()}"
            )

    def stop(self):
        if self.proc.poll() is None:
            self.proc.kill()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            if stream is not None and not stream.closed:
                stream.close()


def _initialize(shim):
    shim.send({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": LEGACY,
            "capabilities": {},
            "clientInfo": {"name": "bundle-gate", "version": "0"},
        },
    })
    got = shim.response(CALL_TIMEOUT_S)
    caps = {}
    if isinstance(got, dict):
        result = got.get("result")
        if isinstance(result, dict):
            caps = result.get("capabilities") or {}
    if not isinstance(caps, dict) or "tools" not in caps:
        _fail(
            "FAIL: initialize handshake failed or timed out. "
            f"Response: {got}"
        )


def _payload(got):
    text = got["result"]["content"][0]["text"]
    return json.loads(text)


def _call(shim, version, name, arguments):
    shim._next_id += 1
    params = {"name": name, "arguments": arguments}
    if version == MODERN:
        params = {
            "_meta": {
                _META_VERSION: MODERN,
                _META_INFO: {"name": "bundle-gate", "version": "0"},
                _META_CAPS: {},
            },
            **params,
        }
    shim.send({
        "jsonrpc": "2.0",
        "id": shim._next_id,
        "method": "tools/call",
        "params": params,
    })
    got = shim.response(CALL_TIMEOUT_S)
    if not isinstance(got, dict) or "error" in got:
        error = "timed out" if not isinstance(got, dict) else got["error"]
        _fail(
            f"FAIL: protocol version {version} rejected or failed: "
            f"{error}"
        )
    try:
        return _payload(got)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as err:
        _fail(
            f"FAIL: protocol version {version} rejected or failed: "
            f"{err}"
        )


def _texts(payload):
    page = payload.get("page") or []
    return "".join(chunk.get("text", "") for chunk in page)


def _hello(shim, version):
    payload = _call(
        shim, version, "execute_python", {"code": "print('hello')"}
    )
    if "hello\n" in _texts(payload) and payload.get("has_more") is False:
        return
    _fail(f"FAIL: hello call returned unexpected payload: {payload}")


def _fail_pages(page_no, token, got, error):
    _fail(
        "FAIL: pagination failed at page "
        f"{page_no} of token {token}: expected {LINE_COUNT} lines, "
        f"got {got} lines. Error: {error}"
    )


def _pages(shim, version):
    lines = _distinct_lines()
    code = "".join(f"print({line!r})\n" for line in lines)
    payload = _call(
        shim, version, "execute_python", {"code": code}
    )
    got = _texts(payload).splitlines()
    token = payload.get("job_token")
    page_no = payload.get("page_no")
    if (
        payload.get("error")
        or payload.get("has_more") is not True
        or not token
    ):
        _fail_pages(page_no, token, len(got), payload.get("error"))
    retries = 0
    pages = 0
    while payload.get("has_more"):
        pages += 1
        if pages > LINE_COUNT or retries > 50:
            _fail_pages(page_no, token, len(got), "no further page")
        current = payload.get("page_no")
        if isinstance(current, int):
            page_no = current
        nxt = 0 if not isinstance(page_no, int) else page_no + 1
        payload = _call(
            shim,
            version,
            "get_output_page",
            {"job_token": token, "page_no": nxt},
        )
        if payload.get("error"):
            _fail_pages(
                payload.get("page_no", page_no),
                token,
                len(got),
                payload.get("error"),
            )
        empty_retry = (
            "page_no" not in payload
            and payload.get("has_more") is True
            and payload.get("page") == []
        )
        if empty_retry:
            retries += 1
            continue
        retries = 0
        got.extend(_texts(payload).splitlines())
    if got != lines:
        _fail_pages(page_no, token, len(got), payload.get("error"))


def _one_version(version, command, args, port, root):
    shim = _Shim(command, args, port, root)
    try:
        _initialize(shim)
        shim.send({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        })
        _hello(shim, version)
        _pages(shim, version)
        shim.close_stdin()
    finally:
        shim.stop()


class BundleShimTests(unittest.TestCase):
    def test_the_packed_shim_pages_both_protocol_versions(self):
        """Hello and all 3000 lines come back in both versions."""
        requested = os.environ.get("VERIFY_BUNDLE", "")
        bundle = os.environ.get("BUNDLE_MCPB", "")
        if requested == "true" and not bundle:
            _fail(
                "FAIL: verify_bundle was requested but "
                "BUNDLE_MCPB is missing or empty"
            )
        if not bundle:
            self.skipTest("verify_bundle did not supply a bundle")
        with tempfile.TemporaryDirectory() as tmp:
            root = _unpack(bundle, Path(tmp))
            command, args = _entry_point(root)
            port = _bridge_port()
            for version in (MODERN, LEGACY):
                try:
                    _one_version(version, command, args, port, root)
                except AssertionError as err:
                    if str(err).startswith("FAIL:"):
                        raise
                    _fail(
                        "FAIL: protocol version "
                        f"{version} rejected or failed: {err}"
                    )


if __name__ == "__main__":
    unittest.main()
