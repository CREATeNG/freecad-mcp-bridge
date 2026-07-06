# MCP Bridge

**MCP Bridge** gives AI agents access to your open FreeCAD session. It runs a small [MCP](https://modelcontextprotocol.io) server *inside* FreeCAD — no binaries, no external dependencies — letting an AI agent execute Python in your live session and see the results.

The server runs only while you toggle it on, and only on your own machine.

---

## Quick start

1. Install **MCP Bridge** from the FreeCAD **Addon Manager** and restart FreeCAD.
2. A **MCP Bridge** toolbar button appears. Click it to start the server — the status bar shows `MCP Bridge: Listening on 127.0.0.1:39280`.
3. Point your MCP client at the bridge with:
   - **Transport:** Streamable HTTP
   - **URL:** `http://127.0.0.1:39280/mcp`

For example, in Claude Code's `.mcp.json`:

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

The server is off until you toggle it on, each session — click the button again to stop it.

That's the whole setup for most clients.

## Claude Desktop

Claude Desktop can't open an HTTP endpoint the way Claude Code and others can, but it can reach the bridge through a small stdio↔HTTP relay (the shim in [`mcp-stdio-shim/`](mcp-stdio-shim/)) — packaged as a bundle you install by double-clicking:

1. Download **`freecad-mcp-bridge.mcpb`** from the [latest release](https://github.com/CREATeNG/freecad-mcp-bridge/releases/latest).
2. Double-click it to install in Claude Desktop.
3. When prompted, set the **port** to match FreeCAD's (default `39280`).

It forwards to the same `http://127.0.0.1:39280/mcp` endpoint.

Alternatively, stdio clients can run the shim directly with node, instead of installing the bundle.

---

## What the AI can do

Once connected, the client has these tools:

- **`execute_python(code)`** — run Python in your FreeCAD session. `App`/`FreeCAD` and `Gui`/`FreeCADGui` are pre-bound. Output (stdout, stderr, exceptions) is returned to the client and mirrored to FreeCAD's **Report view**, so you can watch what the AI runs in real time.
- **`execute_python_file(filepath)`** — read a local `.py` file and run it in the same context.
- **`get_output(page_token)`** — retrieve the remainder of a long-running script's output. Clients call this automatically when a run outlasts the response timeout or produces more than the max page size.

---

## Preferences

**Edit → Preferences → MCP Bridge:**

- **Port** — the loopback port the server listens on (default 39280).
- **Max response timeout** — how long a request waits for output before returning what it has so far; the client fetches any remainder automatically (default 15 s).
- **Max page size** — the most output a single response carries; larger output is split into pages the client fetches automatically (default 64 KB).

---

## Privacy & security

MCP Bridge is designed for **local** control of FreeCAD.

- The bridge opens a loopback-only port (`127.0.0.1`), and only while you toggle it on — an explicit action each session. The open port isn't exclusive to your MCP client — any process on your machine can reach it.
- Your agent can run code in your live FreeCAD session — macro-level access, with no sandbox on the bridge side. That makes your MCP client the guardrail: with one you trust, you stay in control, since it's designed to ask for your approval before running the agent's tool calls.
- The bridge blocks requests whose `Origin` is a web page, so malicious sites can't reach it through your browser.
- The addon collects no telemetry and never connects out to the internet. Any data an AI provider receives is sent by your MCP client, not by FreeCAD.

For security reports, see [SECURITY.md](SECURITY.md).

---

## Other installation methods

Besides the Addon Manager (**Quick start** above):

### Addon Manager — custom repository

1. FreeCAD → Edit → Preferences → Addon Manager → **Custom repositories** → **+**.
2. Enter `https://github.com/CREATeNG/freecad-mcp-bridge`, branch `main` (latest) or a release tag (stable).
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
