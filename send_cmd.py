"""Send Python to a running FreeCAD MCP Bridge over HTTP (loopback test client).

Usage:
  python send_cmd.py "print('hi')"
  python send_cmd.py -f script.py

The bridge must be running (MCP Bridge toolbar toggle on) in FreeCAD. Talks to
127.0.0.1:39280 by default; override the port with FREECAD_MCP_PORT. Stdlib
only — runs on any Python 3, no FreeCAD or pip dependencies.
"""

import json
import os
import sys
import urllib.error
import urllib.request

PORT = int(os.environ.get("FREECAD_MCP_PORT", "39280"))
URL = f"http://127.0.0.1:{PORT}/mcp"


def _rpc(method, params, req_id):
    payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
    req = urllib.request.Request(
        URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        ctype = resp.headers.get("Content-Type", "")
        body = resp.read().decode("utf-8")
    if "text/event-stream" in ctype:
        for line in body.splitlines():
            if line.startswith("data:"):
                body = line[len("data:"):].strip()
                break
    return json.loads(body)


def _tool(name, arguments, req_id):
    resp = _rpc("tools/call", {"name": name, "arguments": arguments}, req_id)
    if "error" in resp:
        raise SystemExit(f"Error: {resp['error'].get('message')}")
    return json.loads(resp["result"]["content"][0]["text"])


def run(name, arguments):
    """Call a tool and stream its output, polling get_output while paged."""
    page = _tool(name, arguments, 1)
    sys.stdout.write(page.get("output", ""))
    req_id = 2
    while page.get("has_more"):
        page = _tool("get_output", {"page_token": page["page_token"]}, req_id)
        sys.stdout.write(page.get("output", ""))
        req_id += 1
    if page.get("error"):
        sys.stderr.write(f"\n{page['error']}\n")


def main(argv):
    if len(argv) < 2:
        print('Usage: python send_cmd.py "code"  |  python send_cmd.py -f script.py')
        return 1
    if argv[1] == "-f":
        if len(argv) < 3:
            print("Error: -f requires a script filepath")
            return 1
        run("execute_python_file", {"filepath": os.path.abspath(argv[2])})
    else:
        run("execute_python", {"code": argv[1]})
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except urllib.error.URLError as exc:
        sys.stderr.write(
            f"Error: cannot reach FreeCAD MCP Bridge at {URL} ({exc}). "
            "Is the bridge running (toolbar toggle on)?\n"
        )
        sys.exit(1)
