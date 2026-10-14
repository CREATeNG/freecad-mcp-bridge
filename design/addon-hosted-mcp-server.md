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
  session, with `App`/`Gui` pre-bound and `__name__` set to `"__main__"`. Stdout and stderr
  are captured as tagged output chunks.
- **`execute_python_file(filepath)`** — initiates a job that reads a local file (absolute
  path) and runs it the same way, with `__file__` set to the file's path. The bridge
  reads the file before the job is queued. If the file cannot be read, the tool returns
  `page: []`, `has_more: false`, and `error` with a message describing the failure — no
  job, no page history, and no `job_token`.
- **`get_output_page(job_token, page_no)`** — fetches the requested page from a job's page
  history. Both arguments are required.

**Result shape**

Every call of the three tools returns a response with the same shape.

- **`page`** — an ordered array of `{stream, text}` chunks captured from the running
  job's stdout and stderr. Read the array in order; concatenate `text` values for a flat
  transcript. Empty when the response is a tool-level `error` (no exec output).
- **`page_no`** — 0-based index of this page in the job's page history; present exactly
  when the response is a stored page (it carries content, or is the job's final page).
  Page 0 is the job's first stored page — usually in the response of the initiating
  call. Empty non-final responses and tool-level `error` responses carry no `page_no`.
- **`has_more`** — always present. When `true`, at least one higher-numbered page exists
  or will exist for this job.
- **`job_token`** — present while the job's page history exists, except on an `error`
  response. Pass it to `get_output_page`. The token names the history for replay and for sub-agents
  that read the same job.
- **`error`** — present when the tool cannot return exec output: `execute_python_file`
  when the bridge cannot read the file (human-readable message), and `get_output_page`
  with `"unknown or expired job_token"` or `"page_no out of range"`. Python failures
  inside a running job are not `error` — they appear as `stderr` chunks in `page`.

**Non-blocking**

`execute_python` and `execute_python_file` (once the file is read successfully) queue a
job for the Qt main thread, then return a response — even if the job is still queued or
running. The response carries the job's first page when the job has produced output or
finished; a quiet job's first response is empty and unnumbered (see *Page history*). A
response always arrives within the configured max response timeout; the agent polls for
remaining output via `get_output_page`. FreeCAD operations can be long-running; this pattern
gives the agent an acknowledgment within that timeout, rather than a silent wait that may
look like a hang.

The timeout bounds the wait for output, not the whole request: its clock starts once the
job is queued, after any file read, and runs while the handler drains the output queue.

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
  (`max_page_size_chars`, configurable, default 64 K characters, that is 65,536), independent of JSON framing. A
  fast-producing job could otherwise return an arbitrarily large single page. The drain
  logic fills a page until the cap is reached; if a single chunk's
  `text` does not fully fit, the remainder is held as pending and continued on the next
  page under the same `stream` — no data loss, cost proportional to the output's size.

**Paging**

When the job has finished and its combined output chunks fit within a single page, the
initiating call returns `has_more: false` and the agent need not poll — no `page_no`
cursor to track.

Large or slow job output is delivered over multiple pages. The first page (`page_no: 0`)
is in the response from `execute_python` (or `execute_python_file`) when the job produced
output or finished within the timeout; otherwise that response is empty and unnumbered,
and page 0 comes from the first `get_output_page` that finds output or the job's end. Later pages come from `get_output_page` with the `job_token` and the next `page_no`. Same contract on HTTP and
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

Only pages with content — and the job's final page — are stored and numbered. An empty
non-final drain is not stored and consumes no page number: the response returns
`page: []`, `has_more: true`, and no `page_no`. `page_no` therefore counts output, not
polling rhythm.

A job is **complete** when its sentinel (see [job-lifecycle.md](job-lifecycle.md)
*Terms*) has been drained into a page. The drain that consumes the
sentinel produces the job's final page, possibly with an empty `page` array, returning
`has_more: false`. While the server runs, `has_more: true` therefore always precedes
at least one more fetchable page: the promise holds by construction.

The history is shared: any client with the `job_token` may read any stored page.
Capability is the token itself (typically passed from the initiating agent to a
sub-agent).

While a job is incomplete, its page history persists until its final page is fetched or
the server stops. A job whose final page is never fetched (an agent that stops polling)
therefore keeps its history until the server stops. After the final page (`has_more: false`), the retention clock starts (see
*Configuration*); the history is then deleted when retention expires or the server stops,
whichever comes first. The client need not have fetched every page before retention
begins — only the job must be complete.

Histories are held in memory; once the final page is fetched, retention bounds a history's
lifetime, not its size — a job's full output is held from job start until retention
expires. Accepted for a local,
single-user tool; users needing a smaller footprint can shorten the retention period.

Requesting an unknown or expired `job_token` returns
`{"page": [], "has_more": false, "error": "unknown or expired job_token"}` — never an
exception.

**Fetching pages**

`get_output_page` requires `job_token` and `page_no`. Let `max` be the highest `page_no` currently in
the history for that job.

- **`page_no` in the history** — return the stored snapshot (idempotent replay).
- **`page_no == max + 1`** and the job is not yet complete — drain forward; if the drain
  yields content or the sentinel, store and return the new page; otherwise return an
  unstored empty response (`page: []`, `has_more: true`, no `page_no`).
- **`page_no > max`** and the job is complete — error.
- **`page_no > max + 1`** and the job is not yet complete — error.
- Out-of-range `page_no` (either case above) returns `"page_no out of range"`.

Because completion is only recorded by the drain that produces the final page, an agent
that follows `has_more` never receives `"page_no out of range"` — that error always
indicates a genuinely out-of-range request.

While a job is running, only one forward poller per `job_token` should request
`page_no == max + 1`; other clients (e.g. sub-agents) should use explicit lower
`page_no` values to read from the history.

**Agent loop**

Append each response's `page` chunks in order. If `error` is present, read it and stop
(tool-level failure — no job output to collect). If the initiating call returns
`has_more: false` without `error`, the job is complete — no polling.

Otherwise, keep `job_token` and apply one rule: call `get_output_page` with `page_no`
equal to the last `page_no` received plus one — 0 if none has been received yet — until
`has_more` is `false`. Empty non-final responses carry no `page_no`, so the same request
naturally repeats; the final page may arrive with an empty `page` array. To retry or
replay a page, request its `page_no` again. `error` is never how a job finishes: on
`get_output_page` it means a dead token or an out-of-range `page_no`.

**Rejected alternative**

MCP Resources (`resources/read`) were evaluated as an alternative. Rejected: identical
data access, but tool support is universal while resource support varies by client.

---

## Connection

**Transport — Streamable HTTP, MCP 2026-07-28 and 2025-11-25**

The in-process server uses the Streamable HTTP transport and speaks
exactly two MCP protocol versions: 2026-07-28, in which every request
carries its own version, and 2025-11-25, which opens with an
`initialize` handshake. Both are served on the same endpoint, each
request in its own version. Each POST carries one JSON-RPC message; a
batched array gets `400` and an invalid-request error. Every reply to a
request is plain JSON (`Content-Type: application/json`), except the
one to `subscriptions/listen` (below). The server returns `405` on GET
and on DELETE: it offers no standalone stream and keeps no sessions.

Job output and paging are delivered on POST through `tools/call`.

**Choosing the version of a request**

The server keeps no state between requests, so each request's own
method, `MCP-Protocol-Version` header and
`_meta["io.modelcontextprotocol/protocolVersion"]` decide its version,
in this order. A rejection is `400 Bad Request` with a JSON-RPC error,
and nothing runs.

- An `initialize` request is 2025-11-25, whatever its headers.
- A missing `MCP-Protocol-Version` header gets a header mismatch
  (`-32020`). Such a request is in a version older than 2025-06-18,
  which the server does not speak.
- A header that differs from the `_meta` version gets a header
  mismatch (`-32020`).
- A `2025-11-25` header with no `_meta` version is 2025-11-25.
- A `2026-07-28` header is 2026-07-28, validated as below.
- Any other version gets the unsupported-version error (`-32022`),
  whose `data` names `2026-07-28` and `2025-11-25` as the supported
  versions and echoes the version requested. So does a `2025-11-25`
  header with a `_meta` version: only 2026-07-28 carries its version in
  `_meta`.

Header names match case-insensitively; header values match exactly.

A notification (no `id`) gets an empty `202` before any of these
checks, in either version, and nothing runs.

**2026-07-28 validation**

A 2026-07-28 request is further checked in this order; the first
failure decides the reply, with `400`.

- A `_meta` lacking `io.modelcontextprotocol/protocolVersion` or
  `io.modelcontextprotocol/clientCapabilities` gets invalid params
  (`-32602`).
- A missing `Mcp-Method` header, or one that differs from the body's
  `method`, gets a header mismatch (`-32020`). So does a `tools/call`
  whose `Mcp-Name` header is missing or differs from the tool name,
  after decoding a value sent in the `=?base64?…?=` form.

A 2025-11-25 request carries no `_meta` version, method or name
headers, and none are checked.

**Methods — 2026-07-28**

- `server/discover` answers with
  `supportedVersions: ["2026-07-28", "2025-11-25"]`,
  `capabilities: {"tools": {}}` and caching hints.
- `tools/list` answers with the three tools and caching hints.
- `tools/call` runs a tool (see *Tools*).
- `subscriptions/listen` is answered on a short SSE stream. The server
  sends no notifications, so the acknowledgment carries an empty
  `notifications` filter; the graceful-closure result follows at once,
  and the stream closes.
- Any other method, `ping` among them, gets `404 Not Found` and
  method-not-found (`-32601`).

**Methods — 2025-11-25**

- `initialize` answers with `protocolVersion: "2025-11-25"`,
  `capabilities: {"tools": {}}` and the server's name and version in
  `serverInfo`. An `initialize` asking for any other version gets the
  same answer: 2025-11-25 is the version the server offers in the
  handshake. The answer issues no session id.
- `tools/list` answers with the three tools.
- `tools/call` runs a tool (see *Tools*).
- `ping` answers with an empty result.
- Any other method gets method-not-found (`-32601`) with `200`, since
  2025-11-25 reserves `404` for an expired session.

**Results**

A 2026-07-28 result carries `resultType: "complete"` and the server's
name and version in `_meta["io.modelcontextprotocol/serverInfo"]`. The
caching hints on `server/discover` and `tools/list` are `ttlMs: 0` and
`cacheScope: "public"`: a client asks again whenever it needs either
answer, one loopback round trip, and never carries one across a
FreeCAD restart or an addon update. A 2025-11-25 result carries none of
these.

In both versions, a tool result is one JSON text block holding the
tool payload. A failed tool call (an unknown or expired `job_token`, an
out-of-range `page_no`, a file that cannot be read) sets
`isError: true` as well as the payload's `error`. An exception raised
by executed code is not a failed tool call: its traceback is ordinary
output. An unknown tool name is a protocol error, invalid params
(`-32602`).

**Transport distinction — direct HTTP clients vs stdio clients**

HTTP-capable clients connect to the endpoint directly. Stdio clients
(via the shim) receive responses as single stdio messages. Tool-call
results and paging are otherwise identical on both transports: each
waits up to the configured timeout for the first page, then polls via
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

Only execution crosses threads; every other request is answered on the HTTP request
thread:

- **Lifecycle messages** (`initialize`, `tools/list`) are answered directly on the
  HTTP request thread as plain JSON, not SSE — pure protocol logic, no FreeCAD state
  involved, so no main-thread hop is needed.

- **Notifications** (requests with no `id`) get an empty `202` reply on the request
  thread.

- **`get_output_page`** is answered on the request thread: it replays a stored page or
  drains the job's output queue, and never touches FreeCAD.

- **Execution calls** (`execute_python` and `execute_python_file`) must run on the Qt
  main thread. For `execute_python_file`, the HTTP handler reads the file first; a
  read failure returns a tool-level `error` on the request thread without queueing a job.
  Otherwise the job is queued to the main-thread dispatcher (see *Exec
  serialization* below); when the job runs, the main thread redirects `sys.stdout`
  and `sys.stderr` to a `TeeWriter` — not before, and not in the HTTP handler.
  The HTTP handler then drains the output queue into the first page and returns it
  (bounds: *Page bounds*; storage and completion: *Page history*). The output queue
  persists as the live buffer, keyed by `job_token`; exec keeps running on the Qt main
  thread, pushing chunks to the queue (a *chunk* is one `{stream, text}` entry from a
  `write()` call, of arbitrary size), and later `get_output_page` calls drain it.
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
correct the script, re-run) reaches hangs an in-process cancel verb cannot, including
executions that cannot be interrupted in-process, at the cost of the session's unsaved
state. If the capability belongs anywhere, it is upstream
(interruptible scripting in FreeCAD) or in the agent, not in the bridge.

---

## Security

**Loopback only, user-toggled**

The server binds to `127.0.0.1` only, never `0.0.0.0`. It starts only when the user
activates the toolbar toggle, and stops when they deactivate it. This satisfies the
Addon Index transparency and deferred-activation requirements.

**Security posture — noted trade-off**

A loopback TCP port is accessible to any local process while active. Named sockets
(Windows named pipes / UNIX domain sockets) can be restricted to the OS user on most
platforms (Qt notes that macOS does not honour socket file permissions); a TCP loopback
port cannot. Accepted on the basis that the attack
surface is local-only and the user controls the toggle.

**Origin header validation — required**

The MCP spec requires servers to validate the `Origin` header on all incoming connections
to prevent DNS rebinding attacks. Rule: reject any request where the `Origin` header is
present and is not `localhost` or `127.0.0.1`.

---

## The stdio shim

**Stdio shim**

Some MCP clients speak only stdio for local servers. A small
zero-dependency Node.js shim (`mcp-stdio-shim/index.js`) ships with
the addon as a stdio ↔ HTTP proxy. Each newline-delimited JSON-RPC
message on stdin is forwarded as an HTTP POST, body unchanged. The
JSON or single-event SSE reply is unwrapped and written back on
stdout as one line. Stdio carries no headers, so the shim sets
`MCP-Protocol-Version` from
`params._meta["io.modelcontextprotocol/protocolVersion"]` when that
field is present, and to `2025-11-25` when it is not. When the
version is `2026-07-28` it also sets `Mcp-Method` from the message's
method and, for `tools/call`, `Mcp-Name` from `params.name`. Node
stdlib only (global `fetch`, Node ≥ 18); Claude Desktop bundles
Node.js, so no user-side runtime install is needed. The shim reads
the port from the `FREECAD_MCP_PORT` environment variable (default
39280). Notifications (no `id`) get no reply. While the bridge
answers, `initialize` is forwarded with every other message. If the
bridge is unreachable, the shim answers `initialize` itself with
`protocolVersion` `2025-11-25`, and `tools/list` from `tools.json`,
a copy of the addon's tool list that release preparation checks
against `tools.py`, so a client started before FreeCAD comes up
cleanly. A `tools/call` then gets a tool result whose `error` says
to start FreeCAD and turn the bridge on; any other request gets a
JSON-RPC error saying the same.

**Shim — no SDK**

The shim is hand-rolled. `@modelcontextprotocol/sdk` was considered and rejected:
the forwarding job (read line → POST → unwrap → write line) is small enough that
the SDK's framing/session machinery isn't warranted, and zero dependencies keeps
the `.mcpb` bundle trivial and auditable.

---

## Implementation choices

**No external dependencies**

The HTTP server uses Python stdlib only (`http.server` (on `socketserver`), `threading`).
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
`get_output_page`. Socket handling and HTTP parsing need no Qt; only the hand-off to the
main thread goes through the Qt-based Executor. `QTcpServer` was considered — more
Qt-idiomatic but requires implementing HTTP parsing manually with no clear benefit.

**Local socket**

Not used. HTTP is the only listener — one endpoint, simpler architecture, and any
developer can test directly with `curl`.

---

## Distribution

**No binary ships with the addon**

No prebuilt machine-code binary is distributed, and nothing is compiled. The only
non-Python code is the shim's plain, readable `index.js`, whose source ships in the addon
repo (`mcp-stdio-shim/`).

**`.mcpb` distribution and the Index binary ban**

The packed `.mcpb` is treated as if the Addon Index binary ban applies to it — a
working assumption. The safer path is taken regardless: the Addon Manager package contains
only source, and the packed `.mcpb` is distributed separately as a GitHub release
asset for Claude Desktop users.

---

## Configuration

Stored at `User parameter:BaseApp/Preferences/Mod/freecad-mcp-bridge`. The port is read
at server start; the timeout, page size and retention are read per request, so changes to
them take effect immediately. Preference page registered as a `.ui` file; appears in Edit →
Preferences → MCP Bridge. FreeCAD wires widgets to the parameter store automatically.

| Parameter | Default | Notes |
|-----------|---------|-------|
| Port | 39280 | Error logged on conflict at startup |
| Max response timeout (s) | 15 | Max wait in `execute_python` and `get_output_page` before returning with `has_more: true` |
| Max page size (K characters) | 64 | Max sum of chunk `text` lengths per page before returning with `has_more: true`, independent of JSON framing and the timeout |
| Page history retention (min) | 5 | How long a job's page history remains readable after its final page (`has_more: false`) |
