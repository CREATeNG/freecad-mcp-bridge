# Development & advanced usage

Developer tips and guidelines for coding and local testing.

| If you are… | Read |
|-------------|------|
| **Installing and using the addon** | [README.md](README.md) |
| **Cutting releases, versioning, Index, CI** | [MAINTAINING.md](MAINTAINING.md) and [TESTING.md](TESTING.md) |
| **Developers** | This document |

For general guidelines on FreeCAD addon development setup—such as cloning, symlinking, or copying files into FreeCAD's versioned `Mod/` directory—please refer to the [FreeCAD Addon Academy](https://freecad.github.io/Addon-Academy/).

This is a small addon — each module in `freecad/mcp_bridge/` carries a docstring describing its role. Start with `http_server.py` and `executor.py` to see the request lifecycle. For the architecture and its rationale, see [design/addon-hosted-mcp-server.md](design/addon-hosted-mcp-server.md); for a walkthrough of how execution behaves, [design/job-lifecycle.md](design/job-lifecycle.md).

---

## 1. Direct CLI testing utility (`send_cmd.py`)

A zero-dependency command-line utility is provided at `send_cmd.py` to test the bridge HTTP endpoint directly without launching an MCP client. It uses Python's standard library to execute code strings or script files in the running FreeCAD session.

*Requires FreeCAD running with the MCP Bridge toolbar toggled on.*

**Run a code string to test the connection (passing the port as the first argument):**
```bash
python send_cmd.py 39280 "print('Hello from the terminal!')"
```

**Run a script file:**
```bash
python send_cmd.py 39280 -f path/to/your/script.py
```

---

## 2. Claude Desktop shim (`mcp-stdio-shim/`)

Claude Desktop cannot directly connect to HTTP MCP servers over the network. The zero-dependency Node shim in `mcp-stdio-shim/` acts as a stdio-to-HTTP relay. It reads JSON-RPC messages from stdin, forwards them to the HTTP server at `http://127.0.0.1:<port>/mcp`, and writes the responses back on stdout.

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
         ]
       }
     }
   }
   ```
---

## 3. Python import guidelines

### Qt/PySide version compatibility
Inside FreeCAD, all GUI and core Qt bindings are exposed via a built-in `PySide` namespace wrapper. Standard addon development guidelines recommend always importing Qt modules from this unified wrapper (e.g., `from PySide.QtCore import ...`) rather than explicitly targeting `PySide6`. Importing `PySide6` directly can conflict with FreeCAD's internally managed Qt namespace. See the [FreeCAD Addon Academy Qt/PySide guide](https://freecad.github.io/Addon-Academy/Guides/Code/Qt).

### Environment separation & import isolation
Python code running outside of FreeCAD (such as `send_cmd.py`) cannot import the `FreeCAD`, `FreeCADGui`, or `PySide` modules.

Additionally, shared protocol modules like `mcp_protocol.py` and `tools.py` must be able to run outside FreeCAD; they therefore must not import FreeCAD or Qt.

---

## 4. Addon icon

The project maintains the following SVG icon assets:

* **Primary Icon (`Resources/Icons/icon.svg`)**: Used for the Addon Manager listing (via `package.xml`) and the in-app toolbar/command.
* **Preferences Icon (`Resources/Icons/preferences-mcp_bridge.svg`)**: Automatically resolved by FreeCAD's Preferences dialog tree view (which searches the icon paths for `preferences-<addon_name>.svg`).
* **Package Fallback (`freecad/mcp_bridge/Resources/Icons/icon.svg`)**: A copy of the primary icon kept in the inner package folder as a fallback for custom manual installations.

All icons are SVG format, which FreeCAD and Qt load natively via QtSvg on Windows, Linux, and macOS without requiring separate `.ico` or `.png` files.
