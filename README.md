<img src="Resources/Icons/icon.svg" alt="MCP Bridge icon" width="64" align="right">

# MCP Bridge

**MCP Bridge** gives AI agents access to your open FreeCAD session. It runs a small [MCP](https://modelcontextprotocol.io) server *inside* FreeCAD, with no binaries and no external dependencies, letting an AI agent execute Python in your live session and see the results.

The server runs only while you toggle it on, and, by default, only on
your own machine.

---

## Quick start

1. Install **MCP Bridge** from the FreeCAD **Addon Manager** and restart FreeCAD.
2. In **Edit → Preferences → MCP Bridge**, click **Generate new
   token**. FreeCAD shows the token once: click **Copy token** and
   keep it for your MCP client, click **Close**, then click **OK**
   in Preferences. The bridge does not start until it has a token.
3. An **MCP Bridge** button is in the toolbar, and the status-bar
   button shows **MCP Off**. Click either one to start the server. The
   first start asks you to accept that connected agents can run
   code with your full permissions. The status-bar button then
   reads **MCP On**, and FreeCAD shows a notification,
   `MCP Bridge: Listening on 127.0.0.1:39280` (`39280` is the
   default port).
4. Connect your MCP client:
   - **Claude Desktop:** install the **MCP Bridge for FreeCAD** extension
     using the steps in [Claude Desktop](#claude-desktop) below.
     There's no file to edit and no URL to type.
   - **Other MCP clients (Claude Code and others):** point the client
     at the bridge with:
     - **Transport:** Streamable HTTP
     - **URL:** `http://127.0.0.1:39280/mcp`, from a loopback entry in
       **Listen addresses**
     - **Header:** `Authorization: Bearer <token>`, with the token
       from step 2

     That's the whole setup for most clients.

     For HTTPS and the stdio shim, see
     [Other MCP clients](https://github.com/CREATeNG/freecad-mcp-bridge/blob/main/USER_GUIDE.md#other-mcp-clients)
     in the user guide.

The server is off until you toggle it on, each session. Click the button again to stop it.

## Claude Desktop

1. Download **`freecad-mcp-bridge.mcpb`** from the
   [latest release](https://github.com/CREATeNG/freecad-mcp-bridge/releases/latest).
2. Open **Settings → Extensions** and install the downloaded bundle.
   On the Linux beta, either drag it into the pane or choose
   **Advanced settings → Install extension** to select the file.
   Accept the installation warning to continue.
3. In the Extensions list, open **MCP Bridge for FreeCAD**.
   **FreeCAD host** is `127.0.0.1` by default (`localhost`, `::1`,
   and `127.x.x.x` count as this machine too), and **port** is
   `39280` by default. Set them to match the entry you connect to
   in FreeCAD's **Listen addresses**, and check that the extension
   is **Enabled**. On the Linux beta, the extension's tools default
   to **Needs approval**.
   - **On this machine:** Paste the token from Quick start's step 2
     into **Bearer token**, and leave **TLS certificate file (PEM)**
     empty.
   - **For another host**, or if a tool call says the bridge serves
     HTTPS and the extension found no `serving-cert.pem`: see
     [Claude Desktop extension](https://github.com/CREATeNG/freecad-mcp-bridge/blob/main/USER_GUIDE.md#claude-desktop-extension)
     in the user guide.

   Reinstalling the extension clears the **Bearer token** setting,
   so paste the token again. After you paste or change the token,
   quit Claude Desktop fully and start it again: the extension reads
   the token only when it starts. Closing its window can leave Claude
   Desktop running in the tray, so quit it from the tray icon's menu.

---

## What the AI can do

Once connected, the agent has these tools:

- **`execute_python(code)`**: run Python in your FreeCAD session.
- **`execute_python_file(filepath)`**: read a local `.py` file and
  run it.
- **`get_output_page(job_token, page_no)`**: fetch the next page of a long-running script's output.

Each run of `execute_python` or `execute_python_file` is one step
in **Edit → Undo**, named "MCP Bridge: run", across every document
it changes. Code that opens its own transactions makes the run a
chain of named steps, so an agent's code should leave transactions
alone. What the code changes after either of two things, in the same
run, can't be undone: turning a document's undo off, or closing
FreeCAD's active transaction before the run has changed anything.
Opening, closing and saving documents aren't undone.

For both `execute_python` and `execute_python_file`: in the **Ask**
permission mode, the default, FreeCAD shows the code in a dialog
before it runs, and for a file its path and whole text. The dialog
has **Unrestricted!**, **Run** and **Don't run**. **Run** runs the
text shown. **Don't run** is the default, and comes back to the
agent as an error.

### Example prompts

With the bridge on, you can ask your agent to build, inspect, or
change a model. For example:

- *"Create a new document with a 40 × 30 × 10 mm mounting plate and a
  6 mm through-hole in the centre."*
- *"List the objects in the active document with their types,
  bounding-box sizes, and volumes."*
- *"Change the through-hole diameter to 8 mm and recompute the
  document."*

---

## User guide

Other MCP clients, HTTPS and certificates, the tools in full, the
permission modes, every preference and other ways to install are in the
[User guide](https://github.com/CREATeNG/freecad-mcp-bridge/blob/main/USER_GUIDE.md).

Developing or maintaining the addon: see
[DEVELOPMENT.md](https://github.com/CREATeNG/freecad-mcp-bridge/blob/main/DEVELOPMENT.md).

---

## Privacy Policy

MCP Bridge is designed for **local** control of FreeCAD.

- The addon and its Claude Desktop shim send no analytics or
  telemetry. The code, file paths, and captured output (`stdout`,
  `stderr`, and exceptions) that pass through them are used for
  three things only: to run the requested code in FreeCAD, to mirror
  the output to FreeCAD's **Report view**, and to return the output
  to your MCP client over HTTP (`127.0.0.1:39280` by default, or the
  entries in **Listen addresses** in Preferences).
- The bridge's page history keeps output pages in FreeCAD's memory
  only, never on disk, and discards them when **Page history
  retention** expires, when you turn the bridge off, or when FreeCAD
  closes. FreeCAD itself writes **Report view** output to its log
  file when FreeCAD's file logging is on.
- The bridge's server inside FreeCAD never connects out to the
  internet or shares data with third parties. Code the agent runs in
  FreeCAD can still access local files or the internet, as any macro
  can.
- By default, the bridge opens a loopback-only port
  (`127.0.0.1:39280`), and only while the bridge is switched on,
  which you do yourself, each session. Any process on your machine
  can reach the open loopback port, not only your MCP client. The
  bridge listens on every entry in **Listen addresses**. Every
  listener requires the bearer token. When a certificate and private
  key are set, every listener serves HTTPS, loopback included; without
  them, loopback entries use HTTP and non-loopback entries are
  refused. The first start asks you to
  accept that connected agents can run code with your full
  permissions. Once you accept a non-loopback entry, the bridge
  listens on it too. Any client that can reach one, with the bearer
  token, can run Python code in FreeCAD as your user account.
- Your agent can run code in your live FreeCAD session: macro-level
  access, with no sandbox on the bridge side. That makes your MCP
  client the guardrail. Use a client you trust, and set it to ask
  before running the agent's tool calls.
- The bridge rejects any request that carries an `Origin` header, so a
  browser page cannot reach it. It also rejects a request whose `Host`
  header, port aside, names neither the address that listener is bound
  to, nor a name its certificate covers, nor a host name in **Listen
  addresses**. A loopback listener accepts the address it is bound to,
  `127.0.0.1`, `localhost` and `[::1]`, but not a name its certificate
  covers.

For privacy questions or security reports, contact chris@createng.com
or see [SECURITY.md](SECURITY.md).
