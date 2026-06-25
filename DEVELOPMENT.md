# Development & advanced usage

Instructions for **developers** — alternative MCP setups, local testing, and tooling.

| If you are… | Read |
|-------------|------|
| **Installing and using the addon** | [README.md](README.md) |
| **Cutting releases, versioning, Index, CI** | [MAINTAINING.md](MAINTAINING.md) and [TESTING.md](TESTING.md) |
| **Developers** | This document |

---

## 1. Building the Rust MCP server from source

Compile the MCP client binary yourself (instead of using the prebuilt files in `bin/`):

1. Ensure you have Rust and Cargo installed.
2. Navigate to the Rust directory:
   ```bash
   cd rust_mcp_server
   ```
3. Build the release binary:
   ```bash
   cargo build --release
   ```
4. The compiled executable will be located at:
   `rust_mcp_server/target/release/freecad-mcp-bridge.exe` (or `freecad-mcp-bridge` on Unix).

This is a **local, single-machine** build. Maintainers produce multi-platform `bin/` artifacts via the Release workflow — see [MAINTAINING.md](MAINTAINING.md).

---

## 2. Alternative Python-based MCP server

Run the MCP server with Python rather than the compiled binary:

1. Install Python 3.9+ and the required dependencies globally on your host environment:
   ```bash
   python -m pip install PySide6 mcp
   ```
2. Add this entry to your AI agent's `mcpServers` configuration (e.g. `claude_desktop_config.json`):
   ```json
   {
     "mcpServers": {
       "freecad-bridge": {
         "command": "python",
         "args": [
           "C:\\Users\\<YourUsername>\\AppData\\Roaming\\FreeCAD\\Mod\\freecad-mcp-bridge\\freecad_mcp_server.py"
         ]
       }
     }
   }
   ```

---

## 3. Direct CLI testing utility (`send_cmd.py`)

Optional command-line utility. Connects directly to the FreeCAD bridge socket (bypassing the MCP server) to execute Python code. Useful for testing connection status independently.

*Requires `PySide6` installed globally on your host terminal environment (`python -m pip install PySide6`).*

**Run a code string to test the connection:**
```bash
python send_cmd.py "print('Hello from the terminal!')"
```

**Run a script file:**
```bash
python send_cmd.py -f path/to/your/script.py
```

---

## 4. Manual macro (no-install alternative)

Run the bridge as a one-off macro without installing the addon:

1. Open FreeCAD.
2. Go to **Macro ➔ Macros... ➔ Create**.
3. Name it `RunBridge.FCMacro`.
4. Copy the entire contents of `freecad/mcp_bridge/bridge.py` from this project, paste it into the editor tab, and save (**Ctrl + S**).
5. Open the macro list and click **Run** on the window you want to control.

---

## Appendix: Qt imports

Code loaded inside FreeCAD (`freecad/mcp_bridge/init_gui.py`, `freecad/mcp_bridge/bridge.py`) uses FreeCAD's `PySide` shim (`PySide.QtCore`, `PySide.QtNetwork`, etc.), which maps to the Qt binding bundled with your FreeCAD install.

The helpers above (`freecad_mcp_server.py`, `send_cmd.py`) run **outside FreeCAD** — in your system Python or terminal — and use standalone `PySide6` for the local socket connection, not FreeCAD's bundled `PySide`.

---

## Appendix: Icons

The addon uses a single **SVG** icon at `Resources/Icons/icon.svg` (repo root). That is sufficient for Windows, Linux, and macOS:

* FreeCAD and Qt load SVG via QtSvg on all platforms (no separate `.ico` or `.png` required).
* `package.xml` references the icon with forward slashes; FreeCAD normalizes paths per OS.
* The same file is used for Addon Manager listing and the in-app toolbar/command.