# Addon-Hosted MCP Server — Analysis & Design

The decision record for the in-process HTTP architecture: the architecture as it
is, and why — including rejected alternatives. History lives in git. For a
plain-language walkthrough of how execution behaves at runtime, see
[job-lifecycle.md](job-lifecycle.md).

---

## What this is

The MCP server runs inside the FreeCAD process, on FreeCAD's bundled Python
(3.11, confirmed on all platforms). No binary ships with the addon; no user-side
dependencies beyond the addon itself.

The addon exposes an HTTP endpoint (Streamable HTTP, MCP spec 2025-03-26). AI clients
connect directly. Claude Desktop, which speaks stdio only for local servers, connects via
a thin TypeScript shim that translates stdio ↔ HTTP.

---

## Settled decisions

**Transport — Streamable HTTP with SSE**
The in-process server uses Streamable HTTP (MCP spec 2025-03-26). Tool call responses
use SSE (`Content-Type: text/event-stream`). The GET endpoint returns 405 — server-initiated
push is not in scope.

`execute_python` and `execute_python_file` are non-blocking: the HTTP handler dispatches
exec to the Qt main thread, collects output for up to the configured timeout (default 15 s),
then returns — even if exec is still running. The response always arrives quickly; the AI polls for remaining
output via `get_output`. FreeCAD operations can be long-running; this pattern gives the
AI an immediate acknowledgment that exec started rather than a silent wait that may look
like a timeout.

**No external dependencies**
The HTTP server uses Python stdlib only (`http.server`, `threading`, `socketserver`).
No pip installs required. FreeCAD's bundled Python 3.11 provides everything needed.

The Python `mcp` package was considered — it would handle MCP protocol, Streamable HTTP
transport, and SSE on the server side, analogous to the Node.js SDK on the shim side.
Rejected: the package is built on asyncio (`uvicorn`, `starlette`, `anyio`), which
conflicts with Qt's event loop. Running it in a background thread with its own asyncio
event loop is possible but significantly complicates the Qt dispatch boundary. The
dependency chain (`pydantic` compiled extension and others) would also need adding to
the Addon Manager allowed packages list. The stdlib implementation is simpler and the
MCP protocol surface for this server is small enough to hand-roll cleanly.

**Threading model**
The HTTP server runs in a background thread. The existing patterns (Qt signals/slots,
`QTimer.singleShot` for deferred main-thread execution) are the right building blocks.
A background thread with Qt signal dispatch is a standard Qt/PySide pattern, well
documented and normal for FreeCAD addons.

Two kinds of request, only one of which crosses threads:

- **Lifecycle messages** (`initialize`, `tools/list`) are answered directly on the
  HTTP request thread — pure protocol logic, no FreeCAD state involved, so no
  main-thread hop is needed.

- **Tool calls** (`execute_python` and `execute_python_file`) must run on the Qt
  main thread. The job is queued to the main-thread dispatcher (see *Exec
  serialization* below); when the job runs, the main thread redirects `sys.stdout`
  and `sys.stderr` to a `TeeWriter` — not before, and not in the HTTP handler.
  The HTTP handler drains the output queue for up to the configured timeout (default 15 s);
  if the sentinel arrives within the timeout, returns `has_more: false` and evicts the
  buffer entry; otherwise returns `has_more: true`. The output queue persists as the page
  buffer (keyed by `page_token`); exec continues running on the Qt main thread, pushing
  chunks to the queue. Subsequent `get_output(page_token)` calls drain the queue until
  exec puts the sentinel and the queue is empty, at which point `has_more: false` is
  returned. Python's GIL is released periodically during exec, allowing the background
  thread to drain the queue in the gaps.

**Exec serialization — dispatcher gate:** exec jobs travel only
through a single FIFO job queue owned by the Executor; the Qt signal carries no payload
and serves purely as a wake-up. The main-thread dispatcher holds a busy latch and drains
the queue one job at a time, each job running to completion before the next is taken.
The latch makes nested dispatcher invocations no-ops — required because executed code
that pumps the Qt event loop (`Gui.updateGui()`, `processEvents()`, modal dialogs) would
otherwise have the next queued exec delivered nested inside the current one. Strict FIFO
and one-at-a-time are enforced by the structure (single queue, single consumer loop)
rather than assumed of Qt's queued-signal delivery. Sequential execution remains the
correct semantics: concurrent scripts would interfere with each other's session state.

**Cancellation of a running exec — out of scope:** the concept is bigger than this
project alone. Native CAD operations are uninterruptible by anyone (a ceiling set by
FreeCAD/OCCT, not by this bridge), stock FreeCAD freezes identically on a runaway
macro, and the agent-side recovery loop (spot the stuck process, kill it, correct the
script, re-run) covers every hang class — more than an in-process cancel verb ever
could. If the capability belongs anywhere, it is upstream (interruptible scripting in
FreeCAD) or in the agent, not in the bridge.

**Loopback only, user-toggled**
The server binds to `127.0.0.1` only, never `0.0.0.0`. It starts only when the user
activates the toolbar toggle, and stops when they deactivate it. This satisfies the
Addon Index transparency and deferred-activation requirements.

**Security posture — noted trade-off**
A loopback TCP port is accessible to any local process while active. Named sockets
(Windows named pipes / UNIX domain sockets) are OS-user-scoped and unreachable from
other local users; a TCP loopback port is not. Accepted on the basis that the attack
surface is local-only and the user controls the toggle. The README must be explicit
about this.

**Origin header validation — required**
The MCP spec requires servers to validate the `Origin` header on all incoming connections
to prevent DNS rebinding attacks. Rule: reject any request where the `Origin` header is
present and is not `localhost` or `127.0.0.1`.

**Claude Desktop shim**
Claude Desktop speaks stdio only for local MCP servers. A small zero-dependency
Node.js shim (`mcp-stdio-shim/index.js`) ships with the addon as a stdio ↔ HTTP
proxy: each newline-delimited JSON-RPC message arriving on stdin is forwarded as an
HTTP POST to the in-process server; the JSON or single-event SSE reply is unwrapped
and written back on stdout as one line. It interprets as little of the protocol as
possible, so protocol additions pass through untouched. Node stdlib only (global
`fetch`, Node ≥ 18); Claude Desktop bundles Node.js — no user-side runtime install
needed.

**Transport distinction — Claude Desktop vs direct clients**
Claude Code and Cursor connect to the HTTP endpoint directly and receive responses over
SSE. Claude Desktop (via shim) receives responses as single stdio messages — SSE does not
exist on that transport. The behavior is otherwise identical: both wait up to the
configured timeout for initial output, then poll via `get_output` if needed. The shim is
only for Claude Desktop users.

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
"Bridge" is accurate at the conceptual level (bridging FreeCAD to the MCP world)
and is distinctive in a crowded namespace.

---

## Design decisions

### Port
Fixed port **39280**, user-configurable in FreeCAD preferences. Clear of all known
FreeCAD MCP ports (9000, 9875, 9876, 10944–10946) and general-purpose dev server ports.
On conflict at startup: log a clear error directing the user to Edit → Preferences →
MCP Bridge. No silent increment. Dynamic port rejected — Claude Code and Cursor users
configure the HTTP endpoint directly; a changing port would require reconfiguration each
session.

### HTTP server implementation
`http.server.ThreadingHTTPServer` + background thread (stdlib, available since Python 3.7).
Each request gets its own thread, allowing concurrent handling of `execute_python` and
`get_output`. No Qt dependency in the HTTP layer. `QTcpServer` was considered — more
Qt-idiomatic but requires implementing HTTP parsing manually with no clear benefit.

### Session management
Stateless for v1 — each POST is independent, no `Mcp-Session-Id` assigned. Sufficient
for the tool calls exposed. Revisit if multi-client scenarios emerge.

### Tool response design — paging pattern
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

LLMs handle this pattern reliably — `page_token`, `next_cursor`, `has_more` are all
patterns trained into modern models from API documentation at scale.

Paging applies to all clients. For SSE clients (Claude Code, Cursor), the SSE response
carries the first chunk and `has_more`; the AI polls via `get_output` for the rest. For
Claude Desktop (via shim), paging is the primary large-output mechanism since SSE is not
visible on that transport.

MCP Resources (`resources/read`) were evaluated as an alternative. Rejected: identical
data access, but tool support is universal while resource support varies by client.

Token lifetime: evicted whenever `has_more: false` is returned — by `execute_python`
itself if exec completes within the timeout, or by `get_output` if polling was needed.
Buffers that are never fully drained accumulate until server stop.

**Page size cap**
A page is also bounded by size (`max_page_size_chars`, configurable, default 64 KB),
independent of the timeout — a fast-producing exec could otherwise return an
arbitrarily large single response, bounded only by how much it printed within the
timeout window. A chunk that doesn't fully fit in the current page is split: the part
that fits is returned, and the untaken remainder is held as `(text, offset)` on the
buffer entry (`pending`) rather than being re-copied — the original string is read
from in place across calls, so splitting one large chunk across many pages costs
proportional to the chunk's total size, not quadratic in the number of pages. No new
lock is needed; `pending` is only ever touched inside the existing per-token
`entry["lock"]`, which already had to exist to serialize concurrent `get_output`
calls for the same token.

### Local socket
Not used. HTTP is the only listener — one endpoint, simpler architecture, and any
developer can test directly with `curl`.

### Shim — no SDK
The shim is hand-rolled. `@modelcontextprotocol/sdk` was considered and rejected:
the forwarding job (read line → POST → unwrap → write line) is small enough that
the SDK's framing/session machinery isn't warranted, and zero dependencies keeps
the `.mcpb` bundle trivial and auditable.

---

## Implementation areas

### In-process HTTP server

- Single endpoint on `127.0.0.1:39280`, supports POST and GET (GET returns 405)
- POST with lifecycle messages (`initialize`, `tools/list`): return `application/json`
- POST with tool calls (`tools/call`): return `text/event-stream` (SSE)
- Session management: stateless for v1
- Origin header validation: required (DNS rebinding protection)
- Port configurable via FreeCAD preferences; error logged on conflict
- Tools: `execute_python(code)`, `execute_python_file(filepath)`, `get_output(page_token)`
- `execute_python` / `execute_python_file` responses include `has_more: bool` and `page_token` when `has_more: true`
- `get_output(page_token)` returns the next chunk; same response shape
- Page buffer: `dict` keyed by `page_token`, each entry `{"queue": queue.Queue(), "lock": threading.Lock()}`
- Sentinel: module-level `SENTINEL = object()` placed on the output queue by the Qt main thread when exec completes — unambiguous end-of-stream signal
- Buffer entries evicted on `has_more: false`; all remaining entries cleared on server stop

### Qt thread boundary

**Lifecycle path (no thread hop):**
- HTTP handler answers `initialize` / `tools/list` directly on the request thread (`mcp_protocol.handle_request`) — no FreeCAD state involved
- HTTP handler: sends `application/json` response

**Non-blocking tool call path:**
- HTTP handler generates `page_token` (UUID4), creates the page-buffer entry (queue, per-token lock, pending slot), puts the job on the Executor's job queue and emits the wake-up signal (see *Exec serialization*)
- HTTP handler acquires the per-token lock, drains the output queue for up to the configured timeout; if sentinel received, returns `has_more: false` and evicts buffer entry; otherwise returns `has_more: true`
- Qt main thread: unaware of paging or chunking — receives signal, redirects `sys.stdout` and `sys.stderr` to a `TeeWriter`, runs `exec()`, puts sentinel on queue, restores `sys.stdout` and `sys.stderr`. Done.
- `TeeWriter` writes to two destinations simultaneously: `output_queue` (for the AI) and `FreeCAD.Console.PrintMessage` (for the FreeCAD Report View). Both receive output in real time as exec runs. The user sees what the AI is executing directly in FreeCAD, in real time.
- Both `sys.stdout` and `sys.stderr` are redirected to the `TeeWriter` for the duration of exec. Exceptions and error output reach the AI and the Report View equally.
- `get_output(page_token)`: if `page_token` not in buffer, returns `{"output": "", "has_more": false, "error": "unknown or expired page_token"}`; otherwise acquires the per-token lock (FIFO — only one reader at a time), reads from the output queue, manages `has_more`; if queue empty and sentinel not seen, waits up to the configured timeout before returning with `has_more: true`; on sentinel, returns remaining output with `has_more: false` and evicts buffer entry

### Shim (`mcp-stdio-shim/index.js`, plain Node.js)

- Reads newline-delimited JSON-RPC from stdin; one HTTP POST per message to `http://127.0.0.1:<port>/mcp` (port from `FREECAD_MCP_PORT`, default 39280)
- Unwraps `application/json` or single-event `text/event-stream` replies; writes the JSON-RPC envelope back as a single stdout line
- Notifications (no `id`) get no reply; if the bridge is unreachable, a JSON-RPC error is synthesized directing the user to the toolbar toggle
- Zero dependencies — Node stdlib only (global `fetch`, Node ≥ 18); no build step

### `.mcpb`

- `manifest.json` + `index.js` + `package.json` — the shim shipped as-is, `entry_point: index.js`, no compile step
- `server.type = "node"` — Node.js ships with Claude Desktop, no user-side install
- Ships in the addon repo alongside Python files
- Claude Desktop users: find in installed addon folder, double-click to install

---

## Configuration

Stored at `User parameter:BaseApp/Preferences/Mod/freecad-mcp-bridge`, read at server
startup. Preference page registered as a `.ui` file; appears in Edit → Preferences →
MCP Bridge. FreeCAD wires widgets to the parameter store automatically.

| Parameter | Widget | Default | Notes |
|-----------|--------|---------|-------|
| Port | `QSpinBox` | 39280 | Error logged on conflict at startup |
| Max response timeout (s) | `QSpinBox` | 15 | Max wait in `execute_python` and `get_output` before returning with `has_more: true` |
| Max page size (KB) | `QSpinBox` | 64 | Max size of a single page's output before returning with `has_more: true`, independent of the timeout |
