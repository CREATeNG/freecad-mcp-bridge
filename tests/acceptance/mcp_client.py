"""A minimal MCP client for the acceptance tests, on urllib alone.

It talks to a real bridge inside a real FreeCAD exactly as an MCP client does:
one JSON-RPC message per POST to BRIDGE_URL (http://127.0.0.1:<port>/mcp).
Lifecycle replies are plain JSON; tools/call replies are a single-event SSE
stream whose `data:` line carries the JSON-RPC response.
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
INITIALIZE_PARAMS = {
    "protocolVersion": "2025-03-26",
    "capabilities": {},
    "clientInfo": {"name": "acceptance-tests", "version": "0"},
}
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
    """Send one raw HTTP request; return its Reply, whatever the status."""
    request = urllib.request.Request(
        url or BRIDGE_URL,
        data=body if method == "POST" else None,
        method=method,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **(headers or {}),
        },
    )
    try:
        with _opener.open(request, timeout=HTTP_TIMEOUT_S) as response:
            return Reply(response.status, response.headers, response.read())
    except urllib.error.HTTPError as error:
        with error:
            return Reply(error.code, error.headers, error.read())


def post(message, headers=None, url=None):
    return send(json.dumps(message).encode("utf-8"), headers=headers, url=url)


_next_id = [0]


def request(method, params=None):
    """A JSON-RPC request message with a fresh id."""
    _next_id[0] += 1
    message = {"jsonrpc": "2.0", "id": _next_id[0], "method": method}
    if params is not None:
        message["params"] = params
    return message


def call_tool(name, arguments, headers=None):
    """Call a tool; return the tool result payload ({page, has_more, ...})."""
    reply = post(
        request("tools/call", {"name": name, "arguments": arguments}),
        headers=headers,
    )
    if reply.status != 200:
        raise AssertionError(f"tools/call {name}: HTTP {reply.status} {reply.body!r}")
    (event,) = reply.sse_events()
    envelope = json.loads(event)
    return json.loads(envelope["result"]["content"][0]["text"])


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
    """Wait for the bridge to answer initialize; True if it did in time."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if post(request("initialize", INITIALIZE_PARAMS)).status == 200:
                return True
        except OSError:
            pass
        time.sleep(0.5)
    return False
