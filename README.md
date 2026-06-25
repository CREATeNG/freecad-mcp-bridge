# MCP Bridge

**MCP Bridge** gives AI tools access to your open FreeCAD session. A flexible, light, safe, and direct bridge by design.

**After you install:** restart FreeCAD, then click **MCP Bridge On/Off** on the toolbar. The bridge is off until you turn it on each session.

**Next step:** configure your AI agent to use the bundled MCP server (see [Configure the MCP server](#configure-the-mcp-server) below).

---

## Quick start (Addon Manager)

1. Click **Install** in Addon Manager.
2. **Restart FreeCAD.** A **MCP Bridge** toolbar appears across workbenches.
3. Click **MCP Bridge On/Off** in the window you want to control. Leave it on while an external tool should talk to FreeCAD.

That is all you need for the in-FreeCAD side. To hook up an AI agent, continue with the MCP setup below.

---

## Configure the MCP server

If you use an AI agent (Claude Desktop, Cursor, a CLI tool, etc.), point it at the bundled MCP server binary. The agent launches this process; it connects to the bridge while the bridge toggle is on in FreeCAD:

1. Locate your client's MCP configuration file:
    *   **Claude Desktop**: `%APPDATA%\Claude\claude_desktop_config.json`
    *   **Cursor**: Add a new local MCP server in **Settings** ➔ **Features** ➔ **MCP**.
2. Add this server entry, pointing to the precompiled binary inside the installed addon folder:

```json
{
  "mcpServers": {
    "mcp-bridge": {
      "command": "C:\\Users\\<YourUsername>\\AppData\\Roaming\\FreeCAD\\Mod\\freecad-mcp-bridge\\bin\\win32\\freecad-mcp-bridge.exe"
    }
  }
}
```

*Substitute the path above for your operating system's standard location if you are on macOS or Linux:*
*   **macOS:** `/Users/<YourUsername>/Library/Application Support/FreeCAD/Mod/freecad-mcp-bridge/bin/macos/freecad-mcp-bridge`
*   **Linux:** `/home/<YourUsername>/.local/share/FreeCAD/Mod/freecad-mcp-bridge/bin/linux/freecad-mcp-bridge`

3. Restart your AI agent or MCP client. The AI will now have native access to:
    *   `execute_python(code)`: Runs Python code inside FreeCAD and returns stdout/stderr/exceptions.
    *   `execute_python_file(filepath)`: Reads a local Python script file from disk and executes it inside FreeCAD. *(Note: filepath must be an absolute path.)*

---

## Two components

This project has two separable parts. What you set up beyond the addon install depends on how you connect.

| Component | What it is | When you need it |
|-----------|------------|------------------|
| **Bridge** | Runs inside FreeCAD. Listens on a local socket and executes Python in your open session. With the addon installed, you toggle it on/off in the UI; you can also run the same logic as a one-off macro. | **Any external connection** — installing this addon is the usual path. Alternatives: [macro tryout](#3-macro-tryout), [custom repository](#1-addon-manager-custom-repository), or [custom module](#2-custom-module) |
| **MCP server** (`bin/` binary) | A small external process your AI agent starts. It speaks MCP on one side and forwards tool calls to the bridge over the same local socket. Shipped with addon and custom-module installs. | **MCP clients only** — Claude Desktop, Cursor, a CLI MCP tool, etc. Not needed for direct socket tools such as [`send_cmd.py`](DEVELOPMENT.md#3-direct-cli-testing-utility-send_cmdpy) |

Without the MCP server, you can still use the bridge with other local tools. Without the bridge running in FreeCAD, nothing outside FreeCAD can connect — including the MCP server.

---

## Privacy & connections

This addon is designed for local-only control of FreeCAD.

* The bridge listens on a local socket **only when you click MCP Bridge On/Off**. Communication uses Qt local sockets (Windows named pipes / UNIX domain sockets) — **no TCP/IP** from the bridge itself.
* While enabled, received Python runs in your open FreeCAD session; stdout, stderr, and exceptions return to the caller over the same local socket.
* While the bridge is on, any **local process** on this machine that can reach the socket may send Python — only enable it when you trust other software on the machine.
* MCP tools such as `execute_python_file` can run Python that reads **file paths you or your client supply** — same trust as running a macro with file access.
* The addon does **not** use network connections or collect telemetry.
* The bundled `bin/` MCP server is a **local** release binary from this repository; it uses stdio (to your MCP client) and the local socket (to FreeCAD). The MCP client runs locally; any data sent to an AI provider goes through that client and service, not through FreeCAD.
* Enabling the bridge is an explicit action each session (toggle on/off).

For security reports, see [SECURITY.md](SECURITY.md).

---

## Other installation methods

Besides the Addon Index install (**Quick start** above), you can use any of the following. For GitHub-based options (1 and 2), set the branch to **`main`** for the latest work or a **release tag** (e.g. `v0.1.11`) for a known snapshot — tags are the safer choice if you want something stable.

### 1. Addon Manager custom repository

1. Open FreeCAD and go to **Edit** ➔ **Preferences** ➔ **Addon manager**.
2. In **Custom repositories**, click **+ (Add)**.
3. Enter `https://github.com/CREATeNG/freecad-mcp-bridge` and set the branch to `main` or a release tag.
4. Click **OK**, then install from **Tools** ➔ **Addon Manager** as usual.

### 2. Custom module

Place the repository in your FreeCAD `Mod` folder as `freecad-mcp-bridge`, then restart:

*   **Windows:** `%APPDATA%\FreeCAD\Mod\`
*   **macOS:** `~/Library/Application Support/FreeCAD/Mod/`
*   **Linux:** `~/.local/share/FreeCAD/Mod/`

Clone or copy from GitHub — `main` or a [release tag](https://github.com/CREATeNG/freecad-mcp-bridge/tags) — into that folder.

### 3. Macro tryout

Run the bridge without installing the addon — handy to test before you commit to an install:

1. Open FreeCAD and go to **Macro** ➔ **Macros...** ➔ **Create**.
2. Name it `RunBridge.FCMacro`.
3. Copy the contents of `freecad/mcp_bridge/bridge.py` from this repository, paste into the macro editor, and save (**Ctrl + S**).
4. Open the macro list and click **Run** on the window you want to control.

Repeat step 4 each session you need the bridge (there is no toolbar toggle on this path).

---

## For developers & maintainers

| If you are… | Start here |
|-------------|------------|
| **Developers** | [DEVELOPMENT.md](DEVELOPMENT.md) — Rust/Python MCP, `send_cmd`, macro |
| **Cutting releases or updating the Index** | [MAINTAINING.md](MAINTAINING.md) — versioning, `release.yml`, Index |
| **CI / install-verify automation** | [TESTING.md](TESTING.md) — FreeCAD test scripts and workflows |

**Repository layout:** `freecad/mcp_bridge/` (addon Python), `bin/` (prebuilt MCP server per OS), `package.xml` (Addon Manager metadata). Tagged releases (`v0.1.x`) are complete install snapshots; `main` may be ahead of the latest tag. See [MAINTAINING.md](MAINTAINING.md) for release policy.