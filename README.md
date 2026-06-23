# FreeCAD Local Socket & MCP Bridge

This project is a standalone and lightweight bridge that allows external scripts, terminals, and AI assistants (using the Model Context Protocol) to execute Python code inside a running instance of FreeCAD.

It operates entirely in-memory using **Windows Named Pipes** (on Windows) or **UNIX Domain Sockets** (on Linux/macOS) via Qt's native `QLocalServer` and `QLocalSocket`, completely bypassing the TCP/IP network layer and avoiding firewall prompts.

---

## Repository Structure

*   `package.xml`: Addon metadata (`other` utility type) for FreeCAD Addon Manager compatibility.
*   `InitGui.py` / `Init.py`: Autoload scripts that register the bridge toggle and add a global toolbar across workbenches.
*   `freecad_bridge.py`: The core local QLocalServer socket listener running inside FreeCAD.
*   `bin/`: Precompiled, self-contained binaries for the MCP client.
    *   `win32/freecad-mcp.exe`: Precompiled Windows binary.

*(For source files, Python MCP scripts, and developer testing tools, see [DEVELOPMENT.md](DEVELOPMENT.md).)*

---

## Setup Instructions

### 1. Install the Addon in FreeCAD
This is a **utility extension** that adds a global toggle across all workbenches.

1. Open FreeCAD and go to **Tools** ➔ **Addon Manager**.
2. Search for `freecad-mcp-bridge` (or `freecad_mcp_bridge`) and click **Install**.
3. Restart FreeCAD.

Once restarted, a persistent **AI Bridge** toolbar will appear in your FreeCAD interface. Click it to toggle the bridge on/off in the active window.

---

### 2. Configure MCP Integration

To allow AI assistants (like Claude Desktop or Cursor) to natively control FreeCAD:

1. Locate your client's MCP configuration file:
    *   **Claude Desktop**: `%APPDATA%\Claude\claude_desktop_config.json`
    *   **Cursor**: Add a new local MCP server in **Settings** ➔ **Features** ➔ **MCP**.
2. Add this server entry, pointing to the precompiled binary inside the installed addon folder:

```json
{
  "mcpServers": {
    "freecad-bridge": {
      "command": "C:\\Users\\<YourUsername>\\AppData\\Roaming\\FreeCAD\\Mod\\freecad-mcp-bridge\\bin\\win32\\freecad-mcp.exe"
    }
  }
}
```

*Substitute the path above for your operating system's standard location if you are on macOS or Linux:*
*   **macOS:** `/Users/<YourUsername>/Library/Application Support/FreeCAD/Mod/freecad-mcp-bridge/bin/macos/freecad-mcp`
*   **Linux:** `/home/<YourUsername>/.local/share/FreeCAD/Mod/freecad-mcp-bridge/bin/linux/freecad-mcp`

3. Restart your AI editor/client. The AI will now have native access to:
    *   `execute_python(code)`: Runs Python code inside FreeCAD and returns stdout/stderr/exceptions.
    *   `execute_python_file(filepath)`: Reads a local Python script file from disk and executes it inside FreeCAD. *(Note: filepath must be an absolute path.)*

---

## Privacy & Connections

This addon is designed for local-only control of FreeCAD. Here is what runs, what connects, and what data leaves your machine.

### Inside FreeCAD

* `freecad_bridge.py` starts a `QLocalServer` on the local socket name `freecad_bridge_socket` **only when you click Start/Stop AI Agent Bridge**.
* Communication uses Qt local sockets (Windows named pipes / UNIX domain sockets). **No TCP/IP network traffic** is used by the bridge itself.
* While the bridge is enabled, received Python code is executed inside your running FreeCAD session. Output (stdout, stderr, exceptions) is returned to the caller over the same local socket.
* The addon does **not** collect telemetry, upload files, or send data to third-party services.

### MCP client (optional, user-configured)

* To connect an AI editor, you separately configure the bundled `freecad-mcp` binary (or the optional Python MCP server in `DEVELOPMENT.md`) in your MCP client settings.
* That MCP process connects to the local FreeCAD socket and forwards tool calls your AI agent makes. It is not started automatically by FreeCAD.
* Any data sent to an AI provider happens through your chosen MCP client and AI service, not through this FreeCAD addon.

### Data storage

* The addon does not persist user documents, credentials, or bridge traffic to disk as part of normal operation.
* MCP client configuration is stored by your AI editor in its own settings files on your system.

### Consent and changes

* Enabling the bridge is an explicit user action each session (toggle on/off).
* If connection behavior or data handling changes in a future release, this section and the changelog will be updated before index/release publication.

For security reports, see [SECURITY.md](SECURITY.md).

---

## Alternative Installation & Testing Methods

### Custom/Pre-Release Testing (Via Addon Manager)
If you want to install from this repository before it is officially listed in the FreeCAD index:
1. Open FreeCAD and go to **Edit** ➔ **Preferences** ➔ **Addon manager**.
2. In the **Custom repositories** section, click the **+ (Add)** button.
3. Enter the Git repository URL: `https://github.com/CREATeNG/freecad-mcp-bridge` and specify the branch (`main`).
4. Click **OK**, then open **Tools** ➔ **Addon Manager**. The bridge will appear in your list ready to install with a single click.

### Manual Installation (Developers / Fallback)
If you prefer to clone or download the files manually:
1. Locate your FreeCAD `Mod` directory:
    *   **Windows:** `%APPDATA%\FreeCAD\Mod\` (typically `C:\Users\<YourUsername>\AppData\Roaming\FreeCAD\Mod\`)
    *   **macOS:** `/Users/<YourUsername>/Library/Application Support/FreeCAD/Mod/`
    *   **Linux:** `~/.local/share/FreeCAD/Mod/`
2. Download or clone this repository directly into that directory under a folder named `freecad-mcp-bridge`.
3. Restart FreeCAD.

For alternative Python script configuration, compiling the binary from source, or using direct CLI testing utilities, see [DEVELOPMENT.md](DEVELOPMENT.md).
