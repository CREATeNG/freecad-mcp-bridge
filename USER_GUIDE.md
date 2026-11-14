# MCP Bridge user guide

This guide is the reference for MCP Bridge. The [README](README.md) has
what the addon is, the quick start for Claude Desktop and for other MCP
clients, what the agent can do, and the Privacy Policy.

---

## Other MCP clients

The basic setup is step 4 of [Quick start](README.md#quick-start) in
the README.

**Over HTTPS.** With your own certificate and private key set in
**Certificate (PEM)** and **Private key (PEM)** in Preferences, the
bridge serves HTTPS on every listener. Use
`https://<HOST>:<PORT>/mcp` as the URL, where `<HOST>` is a name
the bridge accepts and the certificate covers. The bridge accepts
the listener's address (on loopback, also `127.0.0.1`, `localhost`
and `[::1]`), a host name in **Listen addresses**, or, for a
listener beyond this machine, a name the certificate covers. If you
have no certificate, **Generate self-signed certificate** makes
one, covering `127.0.0.1`, `localhost`, each fixed address and host
name in **Listen addresses**, and one IPv4 address for each listed
adapter that is up when you click it: its first that isn't
169.254.x.x. An adapter that is down, or has only a 169.254.x.x
address, adds none. Give the client a copy of the file in
**Certificate (PEM)**, never the private key, and set the client to
trust it, in its setting for a custom or self-signed certificate.

A client that runs stdio servers, and has no such setting, can run
the shim in [`mcp-stdio-shim/`](mcp-stdio-shim/) as one:
`node mcp-stdio-shim/index.js` (Node 18 or later), from the addon's
folder, with `FREECAD_MCP_HOST`, `FREECAD_MCP_PORT`,
`FREECAD_MCP_TOKEN` and `FREECAD_MCP_TLS_CERT_FILE` set to the
host, the port, the token and the path of that copy. The Addon
Manager installs the addon, with the shim, as `freecad-mcp-bridge`
in the `Mod` folder of FreeCAD's user data folder, which
`App.getUserAppDataDir()` prints in FreeCAD's Python console. The
shim sends nothing until the bridge's certificate matches the copy
by its SHA-256 fingerprint.

---

## Claude Desktop extension

The "On this machine" part of step 3 of
[Claude Desktop](README.md#claude-desktop) in the README, in full:

- **On this machine:** Paste the token FreeCAD showed into
  **Bearer token**. The extension writes nothing and reads no
  token file. It reads `serving-cert.pem` in the bridge's
  folder, which the bridge writes at each start with TLS and
  removes at each start without. With that file the extension
  uses HTTPS and checks that the certificate the bridge
  presents has the same SHA-256 fingerprint as that file
  before sending the request. Without it, it uses HTTP. Leave
  **TLS certificate file (PEM)** empty, unless a tool call says
  the bridge serves HTTPS and the extension found no
  `serving-cert.pem`: then set it to a copy of FreeCAD's
  certificate file, not the private key. When that setting is
  set, the extension uses HTTPS with that file in place of
  `serving-cert.pem`, and never HTTP.

For a FreeCAD on another host, follow
[Claude Desktop](README.md#claude-desktop) in the README, with this in
place of step 3's "On this machine":

- **For another host:** Paste the token FreeCAD showed into
  **Bearer token**. The extension writes nothing. Set **TLS
  certificate file (PEM)** to a copy of FreeCAD's certificate
  file, not the private key, so the extension connects over
  HTTPS and checks that the bridge's certificate matches that
  copy's SHA-256 fingerprint before sending the request.

Claude Desktop can't open an HTTP endpoint the way Claude Code and
others can, so the extension runs a small stdio↔HTTP/HTTPS relay (the
shim in [`mcp-stdio-shim/`](mcp-stdio-shim/)). On this machine the
extension forwards to `http://<HOST>:<PORT>/mcp` unless
`serving-cert.pem` is in the bridge's folder, in which case it
forwards to `https://<HOST>:<PORT>/mcp` and pins that file's
certificate by its SHA-256 fingerprint. `localhost` is taken as
`127.0.0.1`. When **TLS certificate file (PEM)** is set, on this
machine too, it forwards to `https://<HOST>:<PORT>/mcp` and pins
that file's certificate, and never uses HTTP. For another host it
forwards to `https://<HOST>:<PORT>/mcp`.

Alternatively, stdio clients can run the shim directly with node,
instead of installing the bundle, as
[Other MCP clients](#other-mcp-clients) describes.

---

## Tools

- **`execute_python(code)`** — run Python in your FreeCAD session.
  `App`/`FreeCAD` and `Gui`/`FreeCADGui` are pre-bound, and
  `__name__` is `"__main__"`, so a script's
  `if __name__ == "__main__":` block runs. Output (stdout, stderr,
  exceptions) is returned to the client and mirrored to FreeCAD's
  **Report view**, so you can watch its output in real time.
- **`execute_python_file(filepath)`** — read a local `.py` file
  (absolute path) and run its text in the same context, with
  `__file__` set to the path.
- **`get_output_page(job_token, page_no)`** — fetch the next page of a long-running script's output. The agent calls it while a response says `has_more: true`, as the tool's description tells it to.

A call to `execute_python` or `execute_python_file` waits up to
**Max response timeout**, dialog included. A run that finishes in that
time comes back in the same reply. If you have not answered by then,
the reply carries a job token, and the agent collects the output, or
the decline, with `get_output_page`.

Each run of `execute_python` or `execute_python_file` is one step
in **Edit → Undo**, named "MCP Bridge: run", across every document
it changes. Code that opens its own transactions makes the run a
chain of named steps, so an agent's code should leave transactions
alone. What the code changes after either of two things, in the same
run, can't be undone: turning a document's undo off, or closing
FreeCAD's active transaction before the run has changed anything.
Opening, closing and saving documents aren't undone.

---

## Permission modes

The dialog FreeCAD shows in the **Ask** mode is described in
[What the AI can do](README.md#what-the-ai-can-do) in the README.

The dialog's **Unrestricted!** button asks if you are really sure. Only
**Yes** switches to the **Unrestricted** mode, in which any code an
agent sends runs with your full permissions, unseen. The On/Off button
on the status bar shows the mode beside **MCP On** or **MCP Off**: a
shield with a tick for Ask, or a warning triangle for Unrestricted. The
menu on the button's arrow has **Permissions**, with **Ask** and
**Unrestricted** under it, each with its icon, above **MCP Bridge
Preferences**. Choosing a mode there switches the same way as
**Permission mode** under **Execution permission** in **Edit →
Preferences → MCP Bridge**: it asks before going Unrestricted, and goes
back to Ask at once. In Preferences too, the choice applies at once,
without OK, and **Cancel** does not undo it, even a switch to
Unrestricted. A click on the button, its icon or its words, turns the
bridge on or off.

---

## Preferences

**Edit → Preferences → MCP Bridge:**

- **Listen addresses** — one list. **+** adds a row, **-** removes the
  selected row, the last one included, and **Edit** (or double-clicking
  a row) edits it in place. With nothing saved, the list shows
  `127.0.0.1:39280`. An adapter row listens, at each start, on that
  adapter's first IPv4 address that isn't 169.254.x.x. A fixed IP
  address listens on exactly that address. A host name should resolve to
  one of the listen addresses. It adds no listener of its own, and
  **Generate self-signed certificate** covers it. Keep a `127.0.0.1` row
  if you use the Claude Desktop extension on this machine.
- While the bridge is on, the page shows where the list differs from
  what it is listening on. Click **OK**, then turn the bridge off and on
  again, to apply the list. A host name applies as soon as it is saved,
  even while the bridge is on. A fixed address this machine does not
  have is skipped. The page marks that row, and the start names it in
  the status bar and in one **Report view** warning, then listens on the
  rest. An adapter that is down, or has no IPv4 address other than
  169.254.x.x, and a wildcard, are named the same way. A port another
  program has taken fails the whole start, and the reason is shown.
- The first start asks you to accept that connected agents can run
  code with your full permissions. A later new address beyond
  loopback asks once, and a changed port does not ask again. For an
  adapter row, the warning shows once for each address the adapter
  hasn't had before.
- **Max response timeout** — how long the bridge waits for a page
  (default 15 s). For code or a file an agent sends, that wait
  includes the dialog, counted from the call. Keep it below the
  client's own request limit, about 60 s for Claude Code. The
  preference allows up to 600 s. The agent fetches the rest with
  `get_output_page`.
- **Max page size** — the most one page may be, as the JSON the agent
  reads, in units of 1,024 characters (default 32, that is 32,768). A
  newline counts as two characters. Larger output is split into pages
  the agent fetches with `get_output_page`.
- **Page history retention** — starts when the job finishes running
  (default 5 min). Each page your agent reads restarts that interval;
  after a gap that long without a fetch, unread output is gone.
- **Bearer token** — every listener requires
  `Authorization: Bearer <token>`. Whenever you click **Generate new
  token**, FreeCAD generates a random token and shows it once. It
  keeps a SHA-256 hash in preferences, not the token. **Set token**
  stores a pasted bearer token the same way, of any length when it
  uses letters, digits and - . _ ~ + /, with = at the end, and does
  not show it again. Nothing is written for the extension to read. A
  bridge that is already running reads the hash again before it
  refuses a token that does not match.
- **Certificate (PEM)** and **Private key (PEM)** — your own certificate
  and private key files. When both are set, every listener serves HTTPS,
  loopback included, and each start writes a copy of the certificate to
  `<home>/.freecad-mcp-bridge/serving-cert.pem` so the Claude Desktop
  extension on this machine can verify it automatically. If that copy
  can't be written, the bridge still starts, and a **Report view**
  warning says the extension on this machine may not connect. Without
  them, loopback entries serve HTTP, and a start with an entry beyond
  loopback fails.
- An MCP client other than the Claude Desktop extension on this
  machine needs a copy of the certificate file, not the private
  key. The extension on this machine reads `serving-cert.pem` and
  pins that file's certificate by its SHA-256 fingerprint.
- **Generate self-signed certificate** — an optional convenience for
  when you have no certificate of your own. This button creates
  `tls-cert.pem` and `tls-key.pem` (the key readable by you only) in
  `<home>/.freecad-mcp-bridge`, covering `127.0.0.1`, `localhost`, each
  fixed address and host name in **Listen addresses**, loopback entries
  such as `::1` included, and one IPv4 address for each listed adapter
  that is up when you click it: its first that isn't 169.254.x.x. It
  lasts 825 days and fills in both fields. OK in Preferences saves them.
  It uses a certificate tool on this machine: openssl, Git for Windows'
  openssl, or Windows PowerShell. You choose the tool and **RSA 4096**
  or **ECDSA P-256**. Where none is found, it makes nothing and says
  where to install openssl. It asks before replacing the current
  certificate, then shows the path, the covered addresses, and the
  expiry.
- A start fails when the certificate is not yet valid or has
  expired, and a start also fails when either file is not set for
  an entry beyond loopback. A listener whose address is not in
  the certificate draws a Report view warning when the certificate
  names no host, and the start still proceeds.
- **Permission mode** — **Ask** (the default) shows you an agent's
  code before it runs. **Unrestricted** runs it unseen, with your
  full permissions. [Permission modes](#permission-modes) has
  the rest.
- **Show** — under **MCP Bridge status bar widget**, shows or hides the
  bridge's On/Off button on FreeCAD's status bar (on by default). The
  **MCP Bridge** toolbar button stays either way.
- **Position** — where the On/Off button sits among the right-hand
  status-bar items: right of FreeCAD's items (default) or left.

---

## Other installation methods

Besides the Addon Manager ([Quick start](README.md#quick-start) in the README):

### Addon Manager — custom repository

1. FreeCAD → Edit → Preferences → Addon Manager → **Custom repositories** → **+**.
2. Enter `https://github.com/CREATeNG/freecad-mcp-bridge`, branch `release`. For the latest `main`, use the manual install below.
3. OK, then install from Tools → Addon Manager.

### Manual (Mod folder)

Copy or clone this repository into your FreeCAD `Mod` folder as `freecad-mcp-bridge`, then restart. The `Mod` folder is under your FreeCAD user-data directory (on FreeCAD 1.1 that's a versioned path, e.g. `…/FreeCAD/v1-1/Mod/`):

- **Windows:** `%APPDATA%\FreeCAD\…\Mod\`
- **macOS:** `~/Library/Application Support/FreeCAD/…/Mod/`
- **Linux:** `~/.local/share/FreeCAD/…/Mod/`
