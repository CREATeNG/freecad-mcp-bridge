# Development & Advanced Usage Guidelines

This document contains instructions for building the bridge from source, running the alternative Python-based MCP server, and using the direct command-line test utility.

---

## 1. Building the Rust MCP Server from Source

If you want to compile the self-contained Rust binary yourself (instead of using the precompiled ones in `bin/`):

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

---

## Qt imports

Code loaded inside FreeCAD (`freecad/mcp_bridge/init_gui.py`, `freecad/mcp_bridge/bridge.py`) uses FreeCAD's `PySide` shim (`PySide.QtCore`, `PySide.QtNetwork`, etc.), which maps to the Qt binding bundled with your FreeCAD install.

The out-of-process helpers below (`freecad_mcp_server.py`, `send_cmd.py`) run in a normal Python environment and use standalone `PySide6` instead.

## Icons

The addon uses a single **SVG** icon at `freecad/mcp_bridge/Resources/Icons/icon.svg`. That is sufficient for Windows, Linux, and macOS:

* FreeCAD and Qt load SVG via QtSvg on all platforms (no separate `.ico` or `.png` required).
* `package.xml` references the icon with forward slashes; FreeCAD normalizes paths per OS.
* The same file is used for Addon Manager listing and the in-app toolbar/command.

---

## 2. Alternative Python-Based MCP Server

If you prefer to run the MCP server using Python rather than the compiled binary:

1. Install Python 3.9+ and the required dependencies globally on your host environment:
   ```bash
   python -m pip install PySide6 mcp
   ```
2. Add this entry to your editor's `mcpServers` configuration (e.g. `claude_desktop_config.json`):
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

## 3. Direct CLI Testing Utility (`send_cmd.py`)

The `send_cmd.py` script is an optional command-line utility. It connects directly to the FreeCAD bridge socket (bypassing the MCP server) to execute Python code. It is useful for testing connection status independently.

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

## 4. Manual Macro (No-Install Alternative)

If you do not want to install any addon folders or global toolbars, you can run the bridge as a one-off macro:

1. Open FreeCAD.
2. Go to **Macro ➔ Macros... ➔ Create**.
3. Name it `RunBridge.FCMacro`.
4. Copy the entire contents of `freecad/mcp_bridge/bridge.py` from this project, paste it into the editor tab, and save (**Ctrl + S**).
5. Open the macro list and click **Run** on the window you want to control.
