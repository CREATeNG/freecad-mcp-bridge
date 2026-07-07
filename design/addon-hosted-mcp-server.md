# Addon-Hosted MCP Server

A FreeCAD addon that lets AI agents drive a live FreeCAD session over the
[Model Context Protocol](https://modelcontextprotocol.io) (MCP). The MCP server provides tools to run jobs
inside FreeCAD's bundled Python: no binary ships with the
addon, and there are no user-side dependencies beyond the addon itself.

---

## Terms

- **Job** — one Python run inside the live FreeCAD session, initiated by a tool call.
  See [job-lifecycle.md](job-lifecycle.md).

- **Chunk** — a job's output is captured in chunks. Each chunk is one `write()` of the job's stdout or stderr.

- **Page** — a capped array of output chunks. A single chunk may span more than one page when
  it exceeds the page size cap.

- **Page history** — the append-only record of every page a job emits. Stays
  retrievable for a configured period of time after the job's final page.

---

## Tools

The MCP server exposes three tools (schemas in `tools.py`).

- **`execute_python(code)`** — initiates a job that runs Python inside the live FreeCAD
  session, with `App`/`Gui` pre-bound. Stdout and stderr are captured as tagged output
  chunks.
- **`execute_python_file(filepath)`** — initiates a job that reads a local file and runs
  it the same way. The bridge reads the file before the job is queued. If the file cannot
  be read, the tool returns `page: []`, `has_more: false`, and `error` with a message
  describing the failure — no job, no page history, and no `job_token`.
- **`get_output_page(job_token, page_no)`** — fetches the requested page from a job's page
  history. Both arguments are required.

**Result shape**

Every tool call returns a response with the same shape.

- **`page`** — an ordered array of `{stream, text}` chunks captured from the running
  job's stdout and stderr. Read the array in order; concatenate `text` values for a flat
  transcript. Empty when the response is a tool-level `error` (no exec output).
- **`page_no`** — 0-based index of this page in the job's page history. Page 0 is the
  first page from the initiating `execute_python` or `execute_python_file` call. Omitted
  when no page history exists (tool-level `error` responses).
- **`has_more`** — always present. When `true`, at least one higher-numbered page exists
  or will exist for this job.
- **`job_token`** — present while the job's page history exists.
  Pass it to `get_output_page`. The token names the history for replay and for sub-agents
  that read the same job.
- **`error`** — present when the tool cannot return exec output: `execute_python_file`
  when the bridge cannot read the file (human-readable message), and `get_output_page`
  with `"unknown or expired job_token"` or `"page_no out of range"`. Python failures
  inside a running job are not `error` — they appear as `stderr` chunks in `page`.

**Non-blocking**

`execute_python` and `execute_python_file` (once the file is read successfully) start a
job on the Qt main thread, then return a response whose first page is in `page` — even if
the job is still running. A
response always arrives within the configured max response timeout; the agent polls for
remaining output via `get_output_page`. FreeCAD operations can be long-running; this pattern
gives the agent an immediate acknowledgment that the job has started rather than a silent
wait that may look like a timeout.

**Page bounds**

A page is closed when either **Response timeout** or **Page size cap** is reached, or when
the job has no more output to deliver. Both bounds apply to `execute_python`,
`execute_python_file`, and `get_output_page` — whichever comes first closes the page (with
`has_more: true` unless the job has also finished).

- **Response timeout** — each drain waits up to the configured max response timeout
  (default 15 s) for more output or for the job to finish before returning. A quiet or
  slow job may therefore yield `has_more: true` with little or no output once the timeout
  elapses.
- **Page size cap** — bounded by the sum of `text` character lengths in the page
  (`max_page_size_chars`, configurable, default 64 KB), independent of JSON framing. A
  fast-producing job could otherwise return an arbitrarily large single page. The drain
  logic fills a page until the cap is reached; if a single chunk's
  `text` does not fully fit, the remainder is held as pending and continued on the next
  page under the same `stream` — no data loss, cost proportional to the output's size.

**Paging**

When the job has finished and its combined output chunks fit within a single page, the
initiating call returns `has_more: false` and the agent need not poll — no `page_no`
cursor to track.

Large or slow job output is delivered over multiple pages. The first page (`page_no: 0`)
is in the response from `execute_python` (or `execute_python_file`); later pages from
`get_output_page` with the `job_token` and the next `page_no`. Same contract on HTTP and
the stdio shim.

```json
// execution call
{ "tool": "execute_python", "code": "..." }
→ {
    "page_no": 0,
    "page": [
      { "stream": "stdout", "text": "Creating box...\n" }
    ],
    "job_token": "abc123",
    "has_more": true
  }

// forward fetch — page_no required
{ "tool": "get_output_page", "job_token": "abc123", "page_no": 1 }
→ {
    "page_no": 1,
    "page": [
      { "stream": "stdout", "text": "...more stdout..." },
      { "stream": "stderr", "text": "DeprecationWarning: ...\n" }
    ],
    "job_token": "abc123",
    "has_more": true
  }

// final page — job output complete; history retained for configured retention period
→ {
    "page_no": 2,
    "page": [
      { "stream": "stderr", "text": "Traceback (most recent call last):\n  ..." }
    ],
    "job_token": "abc123",
    "has_more": false
  }

// execute_python_file — bridge cannot read filepath (no job started)
{ "tool": "execute_python_file", "filepath": "/missing.py" }
→ {
    "page": [],
    "has_more": false,
    "error": "Error reading file '/missing.py': [Errno 2] No such file or directory"
  }
```

**Page history**

Each job has an append-only page history keyed by `job_token`. Every emitted page
is stored as an immutable snapshot (`page_no`, `page`, and the `has_more` value at send
time) before the response is returned. The live output queue feeds forward fetches while
the job is still running; stored snapshots serve replay.

The history is shared: any client with the `job_token` may read any stored page.
Capability is the token itself (typically passed from the initiating agent to a
sub-agent).

While a job is incomplete, its page history persists until the job finishes or the
server stops. After the final page (`has_more: false`), the retention clock starts (see
*Configuration*); the history is then deleted when retention expires or the server stops,
whichever comes first. The client need not have fetched every page before retention
begins — only the job must be complete.

Requesting an unknown or expired `job_token` returns
`{"page": [], "has_more": false, "error": "unknown or expired job_token"}` — never an
exception.

**Fetching pages**

`get_output_page` requires `job_token` and `page_no`. Let `max` be the highest `page_no` currently in
the history for that job.

- **`page_no` in the history** — return the stored snapshot (idempotent replay).
- **`page_no == max + 1`** and the job is not yet complete — drain forward, store, return.
- **`page_no > max`** and the job is complete — error.
- **`page_no > max + 1`** and the job is not yet complete — error.
- Out-of-range `page_no` (either case above) returns `"page_no out of range"`.

While a job is running, only one forward poller per `job_token` should request
`page_no == max + 1`; other clients (e.g. sub-agents) should use explicit lower
`page_no` values to read from the history.

**Agent loop**

Append each response's `page` chunks in order. If `error` is present, read it and stop
(tool-level failure — no job output to collect). If the initiating call returns
`has_more: false` without `error`, the job is complete — no polling.

Otherwise, keep `job_token` and the last committed `page_no`, then call
`get_output_page` with `page_no` equal to that value plus one until `has_more` is
`false`. To retry or replay a page, request the same `page_no` again. Read `error` on
`get_output_page` if present (dead token or out-of-range `page_no`), otherwise a normal
finish.

**Rejected alternative**

MCP Resources (`resources/read`) were evaluated as an alternative. Rejected: identical
data access, but tool support is universal while resource support varies by client.

---

## Connection

**Transport — Streamable HTTP with SSE**

The in-process server uses Streamable HTTP (MCP spec 2025-03-26). Tool call responses
use POST SSE (`Content-Type: text/event-stream`). The server returns HTTP 405 on GET; it
does not offer a server-initiated SSE stream.

Job output and paging are delivered on POST through `tools/call` — not through GET.

**Transport distinction — direct HTTP clients vs stdio clients**

HTTP-capable clients connect to the endpoint directly and receive responses over SSE.
Stdio clients (via the shim) receive responses as single stdio messages — SSE does not
exist on that transport. Tool-call results and paging are otherwise identical on both
transports: each waits up to the configured timeout for the first page, then polls via
`get_output_page` if needed.

**Port**

Fixed port **39280**, user-configurable in FreeCAD preferences.
On conflict at startup: log a clear error directing the user to Edit → Preferences →
MCP Bridge. No silent increment. Dynamic port rejected — HTTP clients configure the
endpoint URL directly; a changing port would require reconfiguration each session.

**Session management**

Stateless at the MCP transport layer — each POST is independent. The server does not assign
`Mcp-Session-Id`. Page history is keyed by `job_token` and is shared among any client that
holds the token.

---

## Execution model

**Threading model**

The HTTP server runs in a background thread; work crosses to the Qt main thread via
queued signal dispatch — a standard Qt/PySide pattern.

Two kinds of request, only one of which crosses threads:

- **Lifecycle messages** (`initialize`, `tools/list`) are answered directly on the
  HTTP request thread — pure protocol logic, no FreeCAD state involved, so no
  main-thread hop is needed.

- **Tool calls** (`execute_python` and `execute_python_file`) must run on the Qt
  main thread. For `execute_python_file`, the HTTP handler reads the file first; a
  read failure returns a tool-level `error` on the request thread without queueing a job.
  Otherwise the job is queued to the main-thread dispatcher (see *Exec
  serialization* below); when the job runs, the main thread redirects `sys.stdout`
  and `sys.stderr` to a `TeeWriter` — not before, and not in the HTTP handler.
  The HTTP handler drains the output queue for up to the configured timeout (default 15 s)
  or until a page fills; if the sentinel arrives first, returns `has_more: false`;
  otherwise returns `has_more: true`. Each returned page is stored in the page history
  (see *Page history*). The output queue persists as the live buffer (keyed by
  `job_token`);
  exec continues running on the Qt main thread, pushing chunks to the queue (a *chunk*
  is one `{stream, text}` entry from a `write()` call, of arbitrary size). Subsequent
  `get_output_page` calls with `page_no == max + 1` drain the queue until exec puts
  the sentinel and the queue is empty, at which point `has_more: false` is returned.
  Draining works while exec still holds the main thread because Python
  releases the GIL periodically during execution — the HTTP thread reads the queue in
  those gaps.

**Exec serialization — dispatcher gate**

Exec jobs travel only through a single FIFO job queue owned by the Executor; the Qt
signal carries no payload and serves purely as a wake-up. The main-thread dispatcher holds a busy latch and drains
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

A plain-language walkthrough of execution — the life of a job from submission through
page history retention — is in [job-lifecycle.md](job-lifecycle.md).

**Cancellation of a running exec — out of scope**

The concept is bigger than this project alone. Stock FreeCAD freezes identically on a
runaway macro, and the AI agent's own recovery loop (spot the stuck process, kill it,
correct the script, re-run) covers every
hang — including executions that cannot be interrupted in-process — more than an
in-process cancel verb ever could. If the capability belongs anywhere, it is upstream
(interruptible scripting in FreeCAD) or in the agent, not in the bridge.

---

## Security

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

---

## The stdio shim

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

**Shim — no SDK**

The shim is hand-rolled. `@modelcontextprotocol/sdk` was considered and rejected:
the forwarding job (read line → POST → unwrap → write line) is small enough that
the SDK's framing/session machinery isn't warranted, and zero dependencies keeps
the `.mcpb` bundle trivial and auditable.

---

## Implementation choices

**No external dependencies**

The HTTP server uses Python stdlib only (`http.server`, `threading`, `socketserver`).
No pip installs required. FreeCAD's bundled Python provides everything needed.

The Python `mcp` package was considered — it would handle MCP protocol, Streamable HTTP
transport, and SSE on the server side, analogous to the Node.js SDK on the shim side.
Rejected: the package is built on asyncio, which conflicts with Qt's event loop —
running it in a background thread with its own event loop is possible but significantly
complicates the Qt dispatch boundary — and its dependency chain would need adding to
the Addon Manager allowed packages list. The stdlib implementation is simpler and the
MCP protocol surface for this server is small enough to hand-roll cleanly.

**HTTP server implementation**

`http.server.ThreadingHTTPServer` + background thread (stdlib).
Each request gets its own thread, allowing concurrent handling of `execute_python` and
`get_output_page`. No Qt dependency in the HTTP layer. `QTcpServer` was considered — more
Qt-idiomatic but requires implementing HTTP parsing manually with no clear benefit.

**Local socket**

Not used. HTTP is the only listener — one endpoint, simpler architecture, and any
developer can test directly with `curl`.

---

## Distribution

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

---

## Configuration

Stored at `User parameter:BaseApp/Preferences/Mod/freecad-mcp-bridge`. The port is read
at server start; the timeout and page size are read per request, so changes to them take
effect immediately. Preference page registered as a `.ui` file; appears in Edit →
Preferences → MCP Bridge. FreeCAD wires widgets to the parameter store automatically.

| Parameter | Default | Notes |
|-----------|---------|-------|
| Port | 39280 | Error logged on conflict at startup |
| Max response timeout (s) | 15 | Max wait in `execute_python` and `get_output_page` before returning with `has_more: true` |
| Max page size (KB) | 64 | Max sum of chunk `text` lengths per page before returning with `has_more: true`, independent of JSON framing and the timeout |
| Page history retention (min) | 5 | How long a job's page history remains readable after its final page (`has_more: false`) |
