"""Send Python to a running FreeCAD MCP Bridge over HTTP (loopback test client).

Usage:
  python send_cmd.py <port> "print('hi')"
  python send_cmd.py <port> -f script.py

The bridge must be running (MCP Bridge toolbar toggle on) in FreeCAD.
Stdlib only — runs on any Python 3, no FreeCAD or pip dependencies.
"""

import json
import os
import sys
import urllib.error
import urllib.request


def _rpc(port, method, params, req_id):
    url = f"http://127.0.0.1:{port}/mcp"
    payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
    req = urllib.request.Request(
        url,
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


def _page_text(result):
    return "".join(chunk.get("text", "") for chunk in result.get("page", []))


def _tool(port, name, arguments, req_id):
    resp = _rpc(port, "tools/call", {"name": name, "arguments": arguments}, req_id)
    if "error" in resp:
        raise SystemExit(f"Error: {resp['error'].get('message')}")
    return json.loads(resp["result"]["content"][0]["text"])


def run(port, name, arguments):
    """Call a tool and stream its output, polling get_output_page while paged."""
    page = _tool(port, name, arguments, 1)
    sys.stdout.write(_page_text(page))
    req_id = 2
    while page.get("has_more"):
        page = _tool(port, "get_output_page", {"job_token": page["job_token"]}, req_id)
        sys.stdout.write(_page_text(page))
        req_id += 1
    if page.get("error"):
        sys.stderr.write(f"\n{page['error']}\n")


def main(argv):
    if len(argv) < 3:
        print('Usage: python send_cmd.py <port> "code"  |  python send_cmd.py <port> -f script.py')
        return 1
    try:
        port = int(argv[1])
    except ValueError:
        print("Error: <port> must be an integer")
        return 1

    if argv[2] == "-f":
        if len(argv) < 4:
            print("Error: -f requires a script filepath")
            return 1
        run(port, "execute_python_file", {"filepath": os.path.abspath(argv[3])})
    else:
        run(port, "execute_python", {"code": argv[2]})
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except urllib.error.URLError as exc:
        port = sys.argv[1] if len(sys.argv) > 1 else "39280"
        sys.stderr.write(
            f"Error: cannot reach FreeCAD MCP Bridge at http://127.0.0.1:{port}/mcp ({exc}). "
            "Is the bridge running (toolbar toggle on)?\n"
        )
        sys.exit(1)
