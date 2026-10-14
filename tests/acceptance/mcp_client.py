"""A minimal MCP client for the acceptance tests, on urllib alone.

It talks to a real bridge inside a real FreeCAD exactly as an MCP 2026-07-28
client does: one JSON-RPC message per POST to BRIDGE_URL
(http://127.0.0.1:<port>/mcp), each request carrying the protocol version,
client info and capabilities in its `_meta`, and the MCP-Protocol-Version,
Mcp-Method and (for tools/call) Mcp-Name headers that mirror its body.
Replies are plain JSON, except subscriptions/listen's SSE stream.
"""

import json
import os
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from urllib.parse import urlsplit, urlunsplit

# Required, so the suite never runs against a FreeCAD session by accident.
BRIDGE_URL = os.environ.get("BRIDGE_URL", "")
if not BRIDGE_URL:
    raise RuntimeError(
        "Set BRIDGE_URL to a running bridge, e.g. http://127.0.0.1:39280/mcp"
    )

HTTP_TIMEOUT_S = 60
PROTOCOL_VERSION = "2026-07-28"
CLIENT_INFO = {"name": "acceptance-tests", "version": "0"}
# Talk to the bridge directly, never through an environment-configured proxy.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class Reply:
    def __init__(self, status, headers, body):
        self.status = status
        self.headers = headers
        self.body = body

    def json(self):
        return json.loads(self.body.decode("utf-8"))

    def sse_events(self):
        """The `data:` payloads of an SSE body, one per event."""
        events = []
        for block in self.body.decode("utf-8").split("\n\n"):
            data = [
                line[len("data:"):].strip()
                for line in block.splitlines()
                if line.startswith("data:")
            ]
            if data:
                events.append("\n".join(data))
        return events


def url_for(path):
    parts = urlsplit(BRIDGE_URL)
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def send(body=b"", method="POST", url=None, headers=None):
    """Send one raw HTTP request; return its Reply, whatever the status.

    A header given as None is left out.
    """
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        **(headers or {}),
    }
    request = urllib.request.Request(
        url or BRIDGE_URL,
        data=body if method == "POST" else None,
        method=method,
        headers={k: v for k, v in headers.items() if v is not None},
    )
    try:
        with _opener.open(request, timeout=HTTP_TIMEOUT_S) as response:
            return Reply(response.status, response.headers, response.read())
    except urllib.error.HTTPError as error:
        with error:
            return Reply(error.code, error.headers, error.read())


def standard_headers(message):
    """The headers a 2026-07-28 client sends with `message`: the protocol
    version, and the method and tool name mirrored from the body."""
    headers = {"MCP-Protocol-Version": PROTOCOL_VERSION}
    if isinstance(message, dict) and "method" in message:
        headers["Mcp-Method"] = message["method"]
        if message["method"] == "tools/call":
            headers["Mcp-Name"] = message["params"]["name"]
    return headers


def post(message, headers=None, url=None):
    """POST a message with the standard headers, which `headers` override
    (None leaves one out)."""
    headers = {**standard_headers(message), **(headers or {})}
    return send(json.dumps(message).encode("utf-8"), headers=headers, url=url)


_next_id = [0]


def request(method, params=None, client_info=CLIENT_INFO):
    """A JSON-RPC request message with a fresh id, its params carrying the
    client's protocol version, info and capabilities in `_meta`."""
    _next_id[0] += 1
    meta = {
        "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
        "io.modelcontextprotocol/clientInfo": client_info,
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    return {
        "jsonrpc": "2.0",
        "id": _next_id[0],
        "method": method,
        "params": {"_meta": meta, **(params or {})},
    }


def tool_result(reply, name="tool"):
    """The tools/call result of a reply ({resultType, content, ...})."""
    if reply.status != 200:
        raise AssertionError(
            f"tools/call {name}: HTTP {reply.status} {reply.body!r}"
        )
    return reply.json()["result"]


def payload(result):
    """The tool result payload ({page, has_more, ...}) a result carries."""
    return json.loads(result["content"][0]["text"])


def call_tool(name, arguments, headers=None):
    """Call a tool; return the tool result payload ({page, has_more, ...})."""
    reply = post(
        request("tools/call", {"name": name, "arguments": arguments}),
        headers=headers,
    )
    return payload(tool_result(reply, name))


def execute(code):
    return call_tool("execute_python", {"code": code})


def execute_file(filepath):
    return call_tool("execute_python_file", {"filepath": filepath})


def get_page(job_token, page_no):
    return call_tool("get_output_page", {"job_token": job_token, "page_no": page_no})


def follow(first):
    """Follow the agent loop from an initiating result until has_more is
    false; return every result received, in order."""
    results = [first]
    last_page_no = first.get("page_no")
    while results[-1]["has_more"]:
        next_no = 0 if last_page_no is None else last_page_no + 1
        result = get_page(first["job_token"], next_no)
        if "error" in result:
            raise AssertionError(f"get_output_page failed: {result}")
        results.append(result)
        if "page_no" in result:
            last_page_no = result["page_no"]
    return results


def text(results):
    """The flat transcript of a run of results."""
    return "".join(chunk["text"] for result in results for chunk in result["page"])


def run(code):
    """Execute code to completion; return its transcript."""
    return text(follow(execute(code)))


@contextmanager
def preference(name, value):
    """Set one of the bridge's integer preferences for the duration, through
    the bridge itself, and restore it after."""
    group = "from freecad.mcp_bridge.constants import PREF_GROUP\np = App.ParamGet(PREF_GROUP)\n"
    before = int(run(group + f"print(p.GetInt({name!r}, -1))").strip())
    run(group + f"p.SetInt({name!r}, {int(value)})")
    try:
        yield
    finally:
        if before == -1:
            run(group + f"p.RemInt({name!r})")
        else:
            run(group + f"p.SetInt({name!r}, {before})")


def wait_until_reachable(timeout_s=30):
    """Wait for the bridge to answer server/discover; True if it did in
    time."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if post(request("server/discover")).status == 200:
                return True
        except OSError:
            pass
        time.sleep(0.5)
    return False
