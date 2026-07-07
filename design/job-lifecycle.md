# The life of a job

A **job** is one `execute_python` (or `execute_python_file`) call: the Python
code to run, a private output channel, and the job token that names it. This walks the whole
lifecycle from the job's point of view, as implemented in `executor.py`
(dispatch) and `paging.py` (output). Terms are collected at the bottom.
Why it works this way — trade-offs and rejected alternatives — lives in
[addon-hosted-mcp-server.md](addon-hosted-mcp-server.md). Agent-facing output
vocabulary (chunk, page, page history) is defined there.

## 1. Born

A job comes into existence on an HTTP request thread, the moment the tool
call arrives: code, a fresh empty output queue, and a token are bundled
together. It exists before any decision about *when* it will run.

## 2. In line

The job is appended to the tail of the job queue. Its position in line is
its entire identity now — nobody watches it individually, and nothing needs
to. Everything ahead of it must fully complete before its turn.

While it waits, someone may already be asking about it: the client got the
token back within the response timeout and may poll `get_output_page`, finding
an empty queue and `has_more: true`. From the outside, *waiting in line*
looks identical to *running but quiet*.

## 3. Taken

The dispatcher's loop reaches the job: it is removed from the queue and
handed to the job runner. This happens only after the job ahead of it has
fully returned — never nested inside it. (If a running job refreshes the
GUI, that can trigger a mid-job wake-up of the dispatcher; the busy latch
makes that wake-up do nothing, so the order still holds.)

## 4. Running

The job owns the main thread. Everything it prints flows into its own
output queue — and the Report View — as it is produced. If its code
refreshes the GUI, repaints happen, but no other job can slip in. Nothing
preempts it; it runs until it is done. Its output can never interleave with
another job's, because the channel it writes to is its own.

## 5. Finished

Success or error, the same ending: an error becomes traceback text in the
output, and the runner's final act is placing the sentinel on the queue —
the job's last word. Control returns to the dispatcher's loop, and the next
job in line gets its turn.

## 6. Afterlife

The job is done, but its page history remains. Every page the bridge already
returned was stored as an immutable snapshot at emit time — replayable by
`page_no`, readable by any client that holds the token. While the job was
still running, forward fetches drained the live output queue into new pages
appended to the history.

When the final page is emitted, the retention clock starts (see
[addon-hosted-mcp-server.md](addon-hosted-mcp-server.md) *Configuration*).
The history stays retrievable for that period whether or not anyone has read
every page yet, then it is deleted. If the bridge stops first, the history is
cleared with everything else and the token dies.

## The unhappy path

If the bridge is stopped while the job is in line, it dies unstarted: its
buffers are cleared, and a client polling its token gets
`"unknown or expired job_token"` — indistinguishable from any other dead
token. If the bridge is stopped while the job is running, the job itself
cannot be interrupted — it runs to completion — but its page history is
cleared at stop, so undelivered output is lost and its token dies the same
way.

## Terms

- **Main thread** — the one thread allowed to touch FreeCAD; also runs the
  GUI. Jobs execute here.
- **Request thread** — a short-lived thread the HTTP server creates per
  incoming request. Jobs are *born* here but never *run* here.
- **Job queue** — the single FIFO line all jobs wait in. The only route a
  job ever travels.
- **Dispatcher** — the loop on the main thread that takes jobs from the
  queue one at a time, each running to completion before the next starts.
- **Busy latch** — a flag meaning "a job is running right now"; it makes a
  mid-job wake-up of the dispatcher a harmless no-op.
- **Output queue** — the job's private channel for everything it prints;
  forward fetches at `page_no == max + 1` drain this into the next page of
  the history.
- **Sentinel** — the end-of-output marker the runner places on the output
  queue as the job's final act; "no more output will ever come."
- **Job token** — the public name of a job's page history; the client
  presents it to `get_output_page` to read or replay pages.