# Development & advanced usage

Developer tips and guidelines for coding and local testing.

| If you are… | Read |
|-------------|------|
| **Installing and using the addon** | [README.md](README.md), then [USER_GUIDE.md](USER_GUIDE.md) |
| **Cutting releases, versioning, Index** | [MAINTAINING.md](MAINTAINING.md) |
| **Running tests locally or in CI** | [TESTING.md](TESTING.md) |
| **Developers** | This document |

For general guidelines on FreeCAD addon development setup—such as cloning, symlinking, or copying files into FreeCAD's versioned `Mod/` directory—please refer to the [FreeCAD Addon Academy](https://freecad.github.io/Addon-Academy/).

This is a small addon. Start with `http_server.py` and `executor.py` to see the request lifecycle. For the architecture and its rationale, see [design/addon-hosted-mcp-server.md](design/addon-hosted-mcp-server.md); for a walkthrough of how execution behaves, [design/job-lifecycle.md](design/job-lifecycle.md).

**Repository layout:** [`freecad/mcp_bridge/`](freecad/mcp_bridge/)
(the addon), [`mcp-stdio-shim/`](mcp-stdio-shim/) (the Claude Desktop
connector source), `package.xml` (Addon Manager metadata). Tagged
releases (`v0.1.x`) are complete snapshots; `main` may be ahead.

---

## 1. Direct CLI testing utility (`send_cmd.py`)

A zero-dependency command-line utility is provided at `send_cmd.py` to test the bridge HTTP endpoint directly without launching an MCP client. It uses Python's standard library to execute code strings or script files in the running FreeCAD session.

*Requires FreeCAD running with the MCP Bridge toolbar toggled on.*
In the **Ask** permission mode FreeCAD shows the code first, and the
output comes once you choose **Run**.

Both commands take the port as their first argument. `39280` is the
default; use your port if it differs.

Both send the bearer token from the `FREECAD_MCP_TOKEN` environment
variable, the token FreeCAD showed when you clicked **Generate new
token**. Without it the bridge refuses the request. `send_cmd.py` reads
no token file. It sends plain HTTP to `127.0.0.1` only. So it needs a
`127.0.0.1` row in **Listen addresses**, and it can't connect while
**Certificate (PEM)** and **Private key (PEM)** are both set.

**Run a code string to test the connection:**
```bash
export FREECAD_MCP_TOKEN='<token>'
python send_cmd.py 39280 "print('Hello from the terminal!')"
```

In PowerShell, set the token with `$env:FREECAD_MCP_TOKEN = '<token>'`
in place of the `export` line.

**Run a script file:**
```bash
python send_cmd.py 39280 -f path/to/your/script.py
```

---

## 2. Claude Desktop shim (`mcp-stdio-shim/`)

Claude Desktop cannot connect directly to a local HTTP MCP server. The
zero-dependency Node shim in `mcp-stdio-shim/` acts as a
stdio-to-HTTP/HTTPS relay. It reads JSON-RPC messages from stdin,
forwards each `tools/call` to the bridge at `<host>:<port>/mcp`, and
writes the responses back on stdout.

It takes its settings from five environment variables:

- `FREECAD_MCP_HOST`: the bridge's host, `127.0.0.1` by default.
  `localhost` is taken as `127.0.0.1`, and `127.x.x.x` and `::1` count
  as this machine.
- `FREECAD_MCP_PORT`: the port, `39280` by default.
- `FREECAD_MCP_TOKEN`: the bearer token. Every call needs it. The shim
  reads no token file.
- `FREECAD_MCP_TLS_CERT_FILE`: the path of a copy of FreeCAD's
  certificate file, not the private key. When it is set, the shim
  connects over HTTPS and pins that certificate by its SHA-256
  fingerprint, and never uses HTTP. With it empty and the host this
  machine, the shim uses HTTPS pinned to `serving-cert.pem` when the
  bridge has written that file, and HTTP when it hasn't. Another host
  needs it set.
- `FREECAD_MCP_TOKEN_DIR`: the folder where the shim looks for
  `serving-cert.pem`, `<home>/.freecad-mcp-bridge` by default.

To run it locally for testing:
1. Ensure Node.js (>=18) is installed.
2. Register the shim in your `claude_desktop_config.json`:
   ```json
   {
     "mcpServers": {
       "freecad": {
         "command": "node",
         "args": [
           "<path-to-your-git-clone>/mcp-stdio-shim/index.js"
         ],
         "env": {
           "FREECAD_MCP_TOKEN": "<token>"
         }
       }
     }
   }
   ```
---

## 3. Python import guidelines

### Qt/PySide version compatibility
Inside FreeCAD, all GUI and core Qt bindings are exposed via a built-in `PySide` namespace wrapper. Standard addon development guidelines recommend always importing Qt modules from this unified wrapper (e.g., `from PySide.QtCore import ...`) rather than explicitly targeting `PySide6`. Importing `PySide6` directly can conflict with FreeCAD's internally managed Qt namespace. See the [FreeCAD Addon Academy Qt/PySide guide](https://freecad.github.io/Addon-Academy/Guides/Code/Qt).

### Environment separation & import isolation
Python code meant to run outside FreeCAD (such as `send_cmd.py`) must not depend on the `FreeCAD`, `FreeCADGui`, or `PySide` modules.

Additionally, the shared modules `mcp_protocol.py`, `tools.py`, `paging.py`, `resources.py` and `constants.py` must be able to run outside FreeCAD; they therefore must not import FreeCAD or Qt.

---

## 4. Addon icon

The project maintains the following SVG icon assets:

* **Primary Icon (`Resources/Icons/icon.svg`)**: Used for the Addon Manager listing (via `package.xml`) and the in-app toolbar/command.
* **Preferences Icon (`Resources/Icons/preferences-mcp_bridge.svg`)**: Automatically resolved by FreeCAD's Preferences dialog tree view (which searches the icon paths for `preferences-<addon_name>.svg`).
* **Package Fallback (`freecad/mcp_bridge/Resources/Icons/icon.svg`)**: A copy of the primary icon kept in the inner package folder as a fallback for custom manual installations.

All icons are SVG format, which FreeCAD and Qt load natively via QtSvg on Windows, Linux, and macOS without requiring separate `.ico` or `.png` files.
