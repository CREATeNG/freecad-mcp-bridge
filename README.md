<img src="Resources/Icons/icon.svg" alt="MCP Bridge icon" width="64" align="right">

# MCP Bridge

**MCP Bridge** gives AI agents access to your open FreeCAD session. It runs a small [MCP](https://modelcontextprotocol.io) server *inside* FreeCAD — no binaries, no external dependencies — letting an AI agent execute Python in your live session and see the results.

The server runs only while you toggle it on, and only on your own machine.

---

## Quick start

1. Install **MCP Bridge** from the FreeCAD **Addon Manager** and restart FreeCAD.
2. An **MCP Bridge** toolbar button appears, and the status-bar button
   shows **MCP Off**. Click either one to start the server. The
   status-bar button then reads **MCP On**, and FreeCAD shows a
   notification, `MCP Bridge: Listening on 127.0.0.1:39280` (`39280` is
   the default port).
3. Connect your MCP client:
   - **Claude Desktop:** install the **FreeCAD MCP Bridge** extension
     using the steps in [Claude Desktop](#claude-desktop) below.
     There's no file to edit and no URL to type.
   - **Other MCP clients (Claude Code and others):** point the client
     at the bridge with:
     - **Transport:** Streamable HTTP
     - **URL:** `http://127.0.0.1:<PORT>/mcp`, where `<PORT>` is the
       **Port** set in Preferences (`39280`, the default)

     For example, in Claude Code's `.mcp.json` (with `39280`, the
     default port):

     ```json
     {
       "mcpServers": {
         "freecad": {
           "type": "http",
           "url": "http://127.0.0.1:39280/mcp"
         }
       }
     }
     ```

     That's the whole setup for most clients.

The server is off until you toggle it on, each session — click the button again to stop it.

## Claude Desktop

1. Download **`freecad-mcp-bridge.mcpb`** from the
   [latest release](https://github.com/CREATeNG/freecad-mcp-bridge/releases/latest).
2. Open **Settings → Extensions** and install the downloaded bundle.
   On the Linux beta, either drag it into the pane or choose
   **Advanced settings → Install extension** to select the file.
   Accept the installation warning to continue.
3. In the Extensions list, open **FreeCAD MCP Bridge**. Its **port**
   is pre-filled with `39280`, the default; set it to match FreeCAD and
   check that the extension is **Enabled**. On the Linux beta, the
   extension's tools default to **Needs approval**.

Claude Desktop can't open an HTTP endpoint the way Claude Code and
others can, so the extension runs a small stdio↔HTTP relay (the shim in
[`mcp-stdio-shim/`](mcp-stdio-shim/)). The extension forwards to
`http://127.0.0.1:<PORT>/mcp`, where `<PORT>` is the port set in it.

Alternatively, stdio clients can run the shim directly with node, instead of installing the bundle.

---

## What the AI can do

Once connected, the agent has these tools:

- **`execute_python(code)`** — run Python in your FreeCAD session. `App`/`FreeCAD` and `Gui`/`FreeCADGui` are pre-bound, and `__name__` is `"__main__"`, so a script's `if __name__ == "__main__":` block runs. Output (stdout, stderr, exceptions) is returned to the client and mirrored to FreeCAD's **Report view**, so you can watch its output in real time.
- **`execute_python_file(filepath)`** — read a local `.py` file (absolute path) and run it in the same context, with `__file__` set to that path.
- **`get_output_page(job_token, page_no)`** — fetch the next page of a long-running script's output. The agent calls it while a response says `has_more: true`, as the tool's description tells it to.

---

## Preferences

**Edit → Preferences → MCP Bridge:**

- **Port** — the loopback port the server listens on (default 39280).
- **Max response timeout** — how long a request waits for output before returning what it has so far; the agent fetches the rest with `get_output_page` (default 15 s).
- **Max page size** — the most one page may be, as the JSON the agent reads, in units of 1,024 characters (default 32, that is 32,768). Larger output is split into pages the agent fetches with `get_output_page`.
- **Page history retention** — starts when the job finishes running
  (default 5 min). Each page your agent reads restarts that interval;
  after a gap that long without a fetch, unread output is gone.
- **Show the On/Off button** — shows or hides the bridge's On/Off
  button on FreeCAD's status bar (on by default). The **MCP Bridge**
  toolbar button stays either way.
- **Position** — where the On/Off button sits among the right-hand
  status-bar items: right of FreeCAD's items (default) or left.

---

## Privacy & security

MCP Bridge is designed for **local** control of FreeCAD.

- The bridge opens a loopback-only port (`127.0.0.1`), and only while you toggle it on — an explicit action each session. The open port isn't exclusive to your MCP client — any process on your machine can reach it.
- Your agent can run code in your live FreeCAD session — macro-level access, with no sandbox on the bridge side. That makes your MCP client the guardrail: with one you trust, set to ask for your approval before running the agent's tool calls, you stay in control.
- The bridge rejects requests whose `Origin` names a host other than `localhost` or `127.0.0.1`, so web pages from other sites can't reach it through your browser.
- The addon collects no telemetry, and the bridge itself never connects out to the internet. Code the agent runs in FreeCAD can still connect to the internet, as any macro can. Any data an AI provider receives is sent by your MCP client, not by the bridge.

For security reports, see [SECURITY.md](SECURITY.md).

---

## Other installation methods

Besides the Addon Manager (**Quick start** above):

### Addon Manager — custom repository

1. FreeCAD → Edit → Preferences → Addon Manager → **Custom repositories** → **+**.
2. Enter `https://github.com/CREATeNG/freecad-mcp-bridge`, branch `release`. For the latest `main`, use the manual install below.
3. OK, then install from Tools → Addon Manager.

### Manual (Mod folder)

Copy or clone this repository into your FreeCAD `Mod` folder as `freecad-mcp-bridge`, then restart. The `Mod` folder is under your FreeCAD user-data directory (on FreeCAD 1.1 that's a versioned path, e.g. `…/FreeCAD/v1-1/Mod/`):

- **Windows:** `%APPDATA%\FreeCAD\…\Mod\`
- **macOS:** `~/Library/Application Support/FreeCAD/…/Mod/`
- **Linux:** `~/.local/share/FreeCAD/…/Mod/`

---

## For developers & maintainers

| If you are… | Start here |
|-------------|------------|
| **Developing the addon** | [DEVELOPMENT.md](DEVELOPMENT.md) |
| **Cutting releases / updating the Index** | [MAINTAINING.md](MAINTAINING.md) |
| **CI / install-verify** | [TESTING.md](TESTING.md) |

**Repository layout:** [`freecad/mcp_bridge/`](freecad/mcp_bridge/) (the addon), [`mcp-stdio-shim/`](mcp-stdio-shim/) (the Claude Desktop connector source), `package.xml` (Addon Manager metadata). Tagged releases (`v0.1.x`) are complete snapshots; `main` may be ahead.
