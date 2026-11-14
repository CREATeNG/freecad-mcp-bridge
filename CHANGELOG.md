## 0.1.29

**What's new since 0.1.28**

### The MCP Bridge addon

- **The MCP Bridge Preferences class now registers with a unique name.**
  It no longer clashes with the Assembly workbench: once Assembly had
  loaded, **Edit → Preferences → MCP Bridge** showed Assembly's settings
  instead of the bridge's, so there was no way to set the bearer token.
  Thanks to @GHswitt for the report, its cause and the fix (#70).
- **Loads on a FreeCAD without Python's ssl module,** such as the
  FreeCAD 1.1.4 snap on the stable channel (revision 2904). Loopback
  listen addresses work there over HTTP, and a start that needs TLS says
  why it can't. Thanks to @lplaurin (#67, #68).
- **Fixed: a start with only one of Certificate (PEM) and Private key
  (PEM) set fell back to HTTP.** It now refuses, loopback included, and
  says which field is missing.
- **Generate self-signed certificate has a Valid for field:** the number
  of days from now the certificate lasts, from 1 to 3650. The default is
  365, where every certificate used to get 825. Where the tool allows,
  the certificate is valid from a day before now, so a client whose
  clock is a little behind still accepts it.
- **The list of listen addresses is now titled Addresses,** since it
  also holds host names, which don't listen.
- **An emptied Addresses list stays empty:** it no longer refills from
  an older profile's **Port** setting, the first save included.

### The Claude Desktop extension

- **Error results put the output last,** as every other result does.

### Documentation

- **A shorter README:** what the addon is, and a quick start for Claude
  Desktop and for other MCP clients. The full reference, with HTTPS,
  every preference and other ways to install, is in the new **User
  guide**.
- **The Addon Manager shows the released README,** from the `release`
  branch, not the one in progress on `main`.

Built by a team: @zewpo and the AI agents Claude (Anthropic), Grok (xAI), Gemini (Google) and Codex (OpenAI).

## 0.1.28

**What's new since 0.1.27**

### The MCP Bridge addon

- **FreeCAD shows an agent's code before it runs:** in the new default
  mode, **Ask**, `execute_python` and `execute_python_file` bring up a
  dialog with the code, and for a file its path, with **Run**, **Don't
  run** (the default) and **Unrestricted!**. The **Unrestricted** mode
  runs code unseen, and asks "Are you really sure?" first. The On/Off
  button on the status bar shows the mode, and its menu switches it.
- **One undo step per run:** each run of `execute_python` or
  `execute_python_file` is one step in **Edit → Undo**, named "MCP
  Bridge: run", across every document it changes. A run that fails keeps
  what ran. Code that opens its own transactions makes the run a chain
  of named steps. What the code changes after either of two things, in
  the same run, can't be undone: turning a document's undo off, or
  closing FreeCAD's active transaction before the run has changed
  anything. Opening, closing and saving documents aren't undone.
- **The bearer token on every listener:** every listener, loopback
  included, requires the token. FreeCAD keeps only its SHA-256 hash.
  **Generate new token** shows a new token once, with **Copy token**,
  and **Set token** takes one made elsewhere.
- **One list of listen addresses:** **Listen addresses** is now a list
  you edit with **+**, **-** and **Edit**. A row can be an adapter,
  which follows that adapter's address, a fixed address, or a host name.
  Wildcard addresses such as `0.0.0.0` are refused. The first start asks
  you to accept that connected agents can run code with your full
  permissions, and a later new address beyond this machine asks once.
- **HTTPS:** set a certificate and private key, and the bridge serves
  HTTPS on every address it listens on, loopback included. Without them
  it serves HTTP on this machine only, and won't start while an address
  beyond it is listed. **Generate self-signed certificate** makes both
  with a tool the machine already has: openssl, Git for Windows' openssl
  or Windows PowerShell.
- **Browsers refused:** the bridge refuses a request that carries an
  `Origin` header. It accepts a `Host` that names the listener's address
  (on loopback, also `127.0.0.1`, `localhost` and `[::1]`), a host name
  in **Listen addresses**, or, beyond this machine, a name the
  certificate covers, and refuses any other.
- **Start lines:** each start writes one line in the **Report view**
  saying where the bridge listens, with a notice for addresses beyond
  this machine. The status bar shows, at every start, that connected
  agents can run code with your full permissions.

### The Claude Desktop extension

- **New name:** the extension is now **MCP Bridge for FreeCAD**.
- **The bearer token:** paste the bridge's token, from **Generate new
  token** or one you set with **Set token**, into the extension's
  **Bearer token** setting, then quit Claude Desktop fully and start it
  again: the extension reads the token only when it starts.
- **The certificate check:** over HTTPS, the extension sends nothing
  until the bridge's certificate matches its copy by SHA-256
  fingerprint. With **TLS certificate file (PEM)** set, it uses that
  file on every host, this machine included. With that setting empty on
  this machine, the extension uses the copy the bridge writes in the
  bridge's folder, and HTTP when there is none. With a new **FreeCAD
  host** setting, the extension reaches FreeCAD on another machine, over
  HTTPS only.
- **Start line:** when it starts, the extension logs the address it
  calls, the certificate file it pins, and whether a bearer token was
  given, never the token.

Built by a team: @zewpo and the AI agents Claude (Anthropic), Grok (xAI), Gemini (Google) and Codex (OpenAI).

## 0.1.27

**What's new since 0.1.26**

- **Listen beyond this machine:** in Preferences → **MCP Bridge**,
  **Listen addresses** replaces the **Port** field: a list of
  `address:port` entries, one per line, `127.0.0.1:39280` by default.
  A port you had changed carries into the default entry. The first
  time the bridge starts with entries beyond this machine, a dialog
  lists them and the risks that still apply, and **Cancel** starts
  nothing.
- **Bearer token and TLS:** beyond loopback, **Require bearer token**
  and **Require TLS (HTTPS)** are on by default, and either can be
  turned off. FreeCAD generates the token, shows it once, and keeps
  only its SHA-256 hash. **Generate new token** replaces it. TLS uses
  your own certificate and private key files. With either missing,
  the bridge doesn't start on such an entry and says what to do.
  **Apply to loopback**, off by default, applies both to loopback
  entries too.
- **Claude Desktop extension:** two optional settings, **Bearer
  token** and **TLS certificate file (PEM)**, for when **Apply to
  loopback** is on. The extension answers Claude Desktop's start-up
  itself and contacts FreeCAD only when a tool is called, so a
  connection problem comes back as that call's result, saying what to
  do.
- **Toolbar stays hidden:** hiding the **MCP Bridge** toolbar now
  lasts across workbench switches and restarts, and **Tools →
  Customize** lists its command under **MCP_Bridge**.
- **Preferences and docs:** the Preferences page has a **Security**
  group and a shorter **Connect an MCP client** note. The README and
  `SECURITY.md` describe the new settings.
- **Behind the scenes:** a release now ships the exact `.mcpb` that
  passed its pull request's Claude Desktop check. The macOS acceptance
  run fails with thread stacks instead of hanging, and Windows CI runs
  FreeCAD with software OpenGL. The release checklist now puts a read
  of the release notes before the version pull request merges.

Built by a team: @zewpo and the AI agents Claude (Anthropic), Grok (xAI), Gemini (Google) and Codex (OpenAI).

## 0.1.26

**What's new since 0.1.25**

- **Claude Desktop extension:** the port field on the extension's page
  now says what's needed on the FreeCAD side and shows the extension's
  version (`0.1.26`). The manifest adds a longer description, plus
  repository, homepage, documentation, support, privacy-policy, and
  compatibility fields.
- **Redirects refused:** if something other than the bridge answers on
  the port and redirects, the Claude Desktop extension now stops and
  says so, instead of following the redirect.
- **Tool titles and annotations:** `execute_python`,
  `execute_python_file`, and `get_output_page` now carry titles
  (`Execute Python`, `Execute Python file`, `Get output page`) and MCP
  annotations. The two execute tools are marked as changing the FreeCAD
  session, and `get_output_page` is marked read-only.
- **Preferences and docs:** the six tooltips on the **MCP Bridge**
  Preferences page are shorter. The README replaces **Privacy &
  security** with a **Privacy Policy**, adds three example prompts, and
  explains that **Max response timeout** is a per-page wait counted from
  when that page starts and that a newline counts as two characters in
  **Max page size**.
- **Behind the scenes:** unit and shim tests now cover the redirect
  refusal, the tool titles and annotations, and the version in the port
  field's help line.

Built by a team: @zewpo and the AI agents Claude (Anthropic), Grok (xAI), Gemini (Google) and Codex (OpenAI).

## 0.1.25

**What's new since 0.1.24**

- **Port-taken warning on Windows:** on Windows, starting the bridge on
  a port another program already held with address reuse could still
  show **MCP On** instead of warning. The bridge now claims its port
  exclusively on Windows, so the bridge fails to start on a taken port
  and shows the port-taken warning there too.
- **Clearer Preferences page:** the client address uses `<PORT>` instead
  of a fixed port number. The **Max response timeout** tooltip says the
  agent fetches the rest of the output with `get_output_page`. The
  **Page history retention** tooltip says each page the agent reads
  restarts the retention time. The default **Position** choice reads
  **Right of FreeCAD's items (default)**.
- **Docs:** the README's **Quick start** gives Claude Desktop users
  their own short path. The README's **Preferences** section lists all
  six settings the page has. Example ports across the docs read as
  variables or as the default (`39280`).
- **Behind the scenes:** the occupied-port acceptance test now holds its
  port with address reuse, the case that let a taken port on Windows go
  unnoticed.

Built by a team: @zewpo and the AI agents Claude (Anthropic), Grok (xAI), Gemini (Google) and Codex (OpenAI).

## 0.1.24

**What's new since 0.1.23**

- **A status-bar button that fits in:** it reads **MCP Off** or **MCP On**, drawn flat like FreeCAD's other status-bar items, and its text turns green when on, in a shade that suits the theme. Click the words to switch the bridge on or off. Click the arrow for a menu whose one item opens the bridge's Preferences page. In Preferences you can hide the button, or place it left of FreeCAD's items.
- **Notifications instead of a popup:** switching off and a failed start now show FreeCAD's own notification too, the failed start as a warning. When the port is already taken, the warning says where to change it.
- **Stdio clients, and the Claude Desktop extension:** when the bridge streams a reply as several events, each now reaches the client as its own message. Before, they were joined into one line the client couldn't read. Update the extension from the attached `freecad-mcp-bridge.mcpb`, or update the shim (`mcp-stdio-shim/`) to this release, to get this.
- **Behind the scenes:** a release refuses to publish without its notes in `CHANGELOG.md`, and the release checklist now includes a check of the extension in Claude Desktop. TESTING.md matches the current test scripts and workflows.

Built by a team: @zewpo and the AI agents Claude (Anthropic), Grok (xAI), Gemini (Google) and Codex (OpenAI).

## 0.1.23

**What's new since 0.1.22**

- **Switching the bridge on is easier to find:** a status-bar button shows **MCP: Off** or **MCP: On**, and clicking it switches the bridge. The Preferences page says how to switch it on, and a successful start shows FreeCAD's own notification.
- **Page history now expires from when a job finishes:** a job's page history is kept for the Page history retention time from when the job finishes, and each page read restarts that time. A gap longer than that between reads loses the output not yet read. Expired output is released even while the bridge is idle.
- **Docs:** the README says more about installing the Claude Desktop extension, and says in full what code run by an agent can reach. Security issues can be reported privately through the repository's **Report a vulnerability** form.
- **Behind the scenes:** each release now tests the packed Claude Desktop extension against the real bridge on Linux, macOS and Windows before tagging, and attaches that same file. A unit test keeps the three version files in step, and TESTING.md describes how testing works. Before this release, the extension was also checked by hand in Claude Desktop on Linux.

Built by a team: @zewpo and the AI agents Claude (Anthropic), Grok (xAI), Gemini (Google) and Codex (OpenAI).

## 0.1.22

**What's new since 0.1.20**

- **MCP versions:** the bridge speaks 2026-07-28 and 2025-11-25, chosen per request, and 2025-03-26 is dropped. A client's handshake is answered with 2025-11-25. A request carrying any other version gets an error naming the two the bridge supports.
- **Claude Desktop extension:** update it from the attached `freecad-mcp-bridge.mcpb`, because earlier versions can't connect to this bridge. It now passes on the protocol version the client asks for.
- **Long output:** pages are smaller and end on a line break wherever one fits, capped by Max page size (32 by default). Each result lists `has_more`, `page_no` and `job_token` before the output.
- **If Max page size shows 64 K characters:** some clients refuse pages that large. Set it to 32 in Edit → Preferences → MCP Bridge.
- **Fixes:**
  - Stopping the bridge drops queued jobs, so they never run.
  - A message with no id gets 202 and runs nothing.
  - A negative page number is reported as out of range.
  - The "Help & feedback" heading in Preferences shows its ampersand.
- **Behind the scenes:** acceptance tests run on every pull request and every push to `main`. Releases now go through a pull request, and a check installs each release from the Index once it's listed.

Built with a team of AI agents: Claude (Anthropic), Grok (xAI), Gemini (Google) and Codex (OpenAI).
