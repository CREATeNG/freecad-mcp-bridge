# Addon-Hosted MCP Server — Decision Record

The decision record for the in-process HTTP architecture: the architecture as it
is, and why — including rejected alternatives. History lives in git. For a
plain-language walkthrough of how execution behaves at runtime, see
[job-lifecycle.md](job-lifecycle.md).

---

## What this is

The MCP server runs inside the FreeCAD process, on FreeCAD's bundled Python. No
binary ships with the addon; no user-side dependencies beyond the addon itself.

The addon exposes an HTTP endpoint (Streamable HTTP, MCP spec 2025-03-26). AI clients
connect directly. Clients that speak only stdio for local servers (e.g. Claude Desktop)
connect via a thin zero-dependency Node.js shim that translates stdio ↔ HTTP.

---

## Decisions

**Transport — Streamable HTTP with SSE**
The in-process server uses Streamable HTTP (MCP spec 2025-03-26). Tool call responses
use SSE (`Content-Type: text/event-stream`). The GET endpoint returns 405 — server-initiated
push is not in scope.

`execute_python` and `execute_python_file` are non-blocking: the HTTP handler dispatches
exec to the Qt main thread, collects output for up to the configured timeout (default 15 s)
or until a page fills (see *Page size cap* below), then returns — even if
exec is still running. The response always arrives quickly; the AI polls for remaining
output via `get_output`. FreeCAD operations can be long-running; this pattern gives the
AI an immediate acknowledgment that exec started rather than a silent wait that may look
like a timeout.

**No external dependencies**
The HTTP server uses Python stdlib only (`http.server`, `threading`, `socketserver`).
No pip installs required. FreeCAD's bundled Python 3.11 provides everything needed.

The Python `mcp` package was considered — it would handle MCP protocol, Streamable HTTP
transport, and SSE on the server side, analogous to the Node.js SDK on the shim side.
Rejected: the package is built on asyncio, which conflicts with Qt's event loop —
running it in a background thread with its own event loop is possible but significantly
complicates the Qt dispatch boundary — and its dependency chain would need adding to
the Addon Manager allowed packages list. The stdlib implementation is simpler and the
MCP protocol surface for this server is small enough to hand-roll cleanly.

**Threading model**
The HTTP server runs in a background thread; work crosses to the Qt main thread via
queued signal dispatch — a standard Qt/PySide pattern.

Two kinds of request, only one of which crosses threads:

- **Lifecycle messages** (`initialize`, `tools/list`) are answered directly on the
  HTTP request thread — pure protocol logic, no FreeCAD state involved, so no
  main-thread hop is needed.

- **Tool calls** (`execute_python` and `execute_python_file`) must run on the Qt
  main thread. The job is queued to the main-thread dispatcher (see *Exec
  serialization* below); when the job runs, the main thread redirects `sys.stdout`
  and `sys.stderr` to a `TeeWriter` — not before, and not in the HTTP handler.
  The HTTP handler drains the output queue for up to the configured timeout (default 15 s)
  or until a page fills; if the sentinel arrives first, returns `has_more: false` and
  evicts the buffer entry; otherwise returns `has_more: true`. The output queue persists
  as the page buffer (keyed by `page_token`); exec continues running on the Qt main
  thread, pushing chunks to the queue (a *chunk* is the text of one `write()` call,
  of arbitrary size). Subsequent `get_output(page_token)` calls drain the queue until
  exec puts the sentinel and the queue is empty, at which point `has_more: false` is
  returned. Draining works while exec still holds the main thread because Python
  releases the GIL periodically during execution — the HTTP thread reads the queue in
  those gaps.

**Exec serialization — dispatcher gate:** exec jobs travel only
through a single FIFO job queue owned by the Executor; the Qt signal carries no payload
and serves purely as a wake-up. The main-thread dispatcher holds a busy latch and drains
the queue one job at a time, each job running to completion before the next is taken.
The latch makes nested dispatcher invocations no-ops. It is required because executed
code can ask Qt to process pending events mid-run — "pumping" the event loop, which is
what `Gui.updateGui()`, `processEvents()`, and modal dialogs do — and delivering
pending events includes starting the next queued exec. That delivery re-enters the
dispatcher on the same call stack — recursion — so without the latch the next job
would run nested inside the current one. Strict FIFO
and one-at-a-time are enforced by the structure (single queue, single consumer loop)
rather than assumed of Qt's queued-signal delivery. Sequential execution remains the
correct semantics: concurrent scripts would interfere with each other's session state.

**Cancellation of a running exec — out of scope:** the concept is bigger than this
project alone. Stock FreeCAD freezes identically on a runaway macro, and the AI client's
own recovery loop (spot the stuck process, kill it, correct the script, re-run) covers every
hang — including executions that cannot be interrupted in-process — more than an
in-process cancel verb ever could. If the capability belongs anywhere, it is upstream
(interruptible scripting in FreeCAD) or in the AI client, not in the bridge.

**Loopback only, user-toggled**
The server binds to `127.0.0.1` only, never `0.0.0.0`. It starts only when the user
activates the toolbar toggle, and stops when they deactivate it. This satisfies the
Addon Index transparency and deferred-activation requirements.

**Security posture — noted trade-off**
A loopback TCP port is accessible to any local process while active. Named sockets
(Windows named pipes / UNIX domain sockets) are OS-user-scoped and unreachable from
other local users; a TCP loopback port is not. Accepted on the basis that the attack
surface is local-only and the user controls the toggle.

**Origin header validation — required**
The MCP spec requires servers to validate the `Origin` header on all incoming connections
to prevent DNS rebinding attacks. Rule: reject any request where the `Origin` header is
present and is not `localhost` or `127.0.0.1`.

**Stdio shim**
Some MCP clients speak only stdio for local servers. A small zero-dependency
Node.js shim (`mcp-stdio-shim/index.js`) ships with the addon as a stdio ↔ HTTP
proxy: each newline-delimited JSON-RPC message arriving on stdin is forwarded as an
HTTP POST to the in-process server; the JSON or single-event SSE reply is unwrapped
and written back on stdout as one line. It interprets as little of the protocol as
possible, so protocol additions pass through untouched. Node stdlib only (global
`fetch`, Node ≥ 18); Claude Desktop bundles Node.js — no user-side runtime install
needed. The shim reads the port from the `FREECAD_MCP_PORT` environment variable
(default 39280). Notifications (no `id`) get no reply; if the bridge is unreachable,
the shim synthesizes a JSON-RPC error directing the user to the toolbar toggle.

**Transport distinction — direct HTTP clients vs stdio clients**
HTTP-capable clients connect to the endpoint directly and receive responses over SSE.
Stdio clients (via the shim) receive responses as single stdio messages — SSE does not
exist on that transport. The behavior is otherwise identical: both wait up to the
configured timeout for initial output, then poll via `get_output` if needed.

**No binary ships with the addon**
No prebuilt machine-code binary is distributed. The only non-Python artifact is the
shim's plain, readable `index.js`, whose source ships in the addon repo
(`mcp-stdio-shim/`); nothing is compiled.

**`.mcpb` distribution and the Index binary ban**
The packed `.mcpb` is treated as if the Addon Index binary ban applies to it — a
working assumption, stated to the maintainer on the Index issue, where it can be
corrected. The safer path is taken regardless: the Addon Manager package contains
only source, and the packed `.mcpb` is distributed separately as a GitHub release
asset for Claude Desktop users.

**Project name — `freecad-mcp-bridge`**
"Bridge" is accurate at the conceptual level: bridging FreeCAD to the MCP world.

**Port**
Fixed port **39280**, user-configurable in FreeCAD preferences.
On conflict at startup: log a clear error directing the user to Edit → Preferences →
MCP Bridge. No silent increment. Dynamic port rejected — HTTP clients configure the
endpoint URL directly; a changing port would require reconfiguration each session.

**HTTP server implementation**
`http.server.ThreadingHTTPServer` + background thread (stdlib).
Each request gets its own thread, allowing concurrent handling of `execute_python` and
`get_output`. No Qt dependency in the HTTP layer. `QTcpServer` was considered — more
Qt-idiomatic but requires implementing HTTP parsing manually with no clear benefit.

**Session management**
Stateless — each POST is independent, no `Mcp-Session-Id` assigned. Sessions would add
multi-client isolation; not needed for a single local client.

**Tool response design — paging pattern**
Large output from exec calls (printing large arrays, verbose script runs) is handled via
a `page_token` parameter on `execute_python`, with a dedicated `get_output` tool for retrieval.

```json
// execution call
{ "tool": "execute_python", "code": "..." }
→ { "output": "...(first chunk)...", "page_token": "abc123", "has_more": true }

// retrieval call — same token each time
{ "tool": "get_output", "page_token": "abc123" }
→ { "output": "...(next chunk)...", "page_token": "abc123", "has_more": true }

// final page
→ { "output": "...(last chunk)...", "has_more": false }
```

`has_more` is always present in the response — the AI reads a boolean, not an absent
field. `get_output` is a distinct tool: it only retrieves buffered output and takes no
code parameter, so there is no ambiguity about what it does on each call.

LLMs handle this pattern reliably — it is ubiquitous in API design.

Paging applies to all clients. For direct HTTP clients, the SSE response carries the
first chunk and `has_more`; the AI polls via `get_output` for the rest. For stdio
clients (via the shim), paging is the primary large-output mechanism since SSE is not
visible on that transport.

MCP Resources (`resources/read`) were evaluated as an alternative. Rejected: identical
data access, but tool support is universal while resource support varies by client.

Token lifetime: evicted whenever `has_more: false` is returned — by `execute_python`
itself if exec completes within the timeout, or by `get_output` if polling was needed.
Buffers that are never fully drained accumulate until server stop. An unknown or
expired `page_token` returns
`{"output": "", "has_more": false, "error": "unknown or expired page_token"}` —
never an exception.

**Page size cap**
A page is also bounded by size (`max_page_size_chars`, configurable, default 64 KB),
independent of the timeout — a fast-producing exec could otherwise return an
arbitrarily large single response. A chunk that doesn't fit the current page is split
across pages and delivered by subsequent `get_output` calls — no data loss, cost
proportional to the output's size.

**Local socket**
Not used. HTTP is the only listener — one endpoint, simpler architecture, and any
developer can test directly with `curl`.

**Shim — no SDK**
The shim is hand-rolled. `@modelcontextprotocol/sdk` was considered and rejected:
the forwarding job (read line → POST → unwrap → write line) is small enough that
the SDK's framing/session machinery isn't warranted, and zero dependencies keeps
the `.mcpb` bundle trivial and auditable.

---

## Configuration

Stored at `User parameter:BaseApp/Preferences/Mod/freecad-mcp-bridge`. The port is read
at server start; the timeout and page size are read per request, so changes to them take
effect immediately. Preference page registered as a `.ui` file; appears in Edit →
Preferences → MCP Bridge. FreeCAD wires widgets to the parameter store automatically.

| Parameter | Default | Notes |
|-----------|---------|-------|
| Port | 39280 | Error logged on conflict at startup |
| Max response timeout (s) | 15 | Max wait in `execute_python` and `get_output` before returning with `has_more: true` |
| Max page size (KB) | 64 | Max size of a single page's output before returning with `has_more: true`, independent of the timeout |
