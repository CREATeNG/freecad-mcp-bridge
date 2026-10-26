"""Page history for streamed job output.

Each job gets a history entry keyed by a job_token: an output queue the Qt
main thread pushes (stream, text) chunks (then SENTINEL) onto, a per-entry
lock so only one reader drains at a time, and an append-only list of stored
page snapshots. fetch() returns the page at a given page_no, draining the
live queue forward when that page is the next one and the job is still
running. A page is bounded by time (timeout_ms) and by the length of
the JSON tool result (page_size_chars). Adjacent chunks of the same
stream are joined as the page is built. A chunk that does not fit is
held in "pending", with its stream, and continued on the next page.

Only pages with content — and the job's final page — are stored and numbered;
an empty non-final drain is not stored and consumes no page number. Page
delivery is complete once SENTINEL has been drained into a page; that final
page carries has_more=False. The retention clock starts when execution
finishes, and each valid fetch after that restarts the same interval.

This module must be able to run outside FreeCAD.
"""

import json
import queue
import socket
import sys
import threading
import time
import uuid
from http.server import ThreadingHTTPServer

from freecad.mcp_bridge.mcp_protocol import serialized_page_length

# End-of-output marker placed on a job's queue by the executor.
SENTINEL = object()

_BUFFER = {}
_BUFFER_LOCK = threading.Lock()


def start_page():
    """Create a new job history entry; return (job_token, output_queue)."""
    _sweep_expired()
    token = uuid.uuid4().hex
    output_queue = queue.Queue()
    with _BUFFER_LOCK:
        _BUFFER[token] = {
            "queue": output_queue,
            "lock": threading.Lock(),
            # Writes held for the next page, oldest first. A cut at a
            # newline can hold several, so a line built from many writes
            # stays together.
            "pending": [],
            "history": [],  # stored page snapshots, indexed by page_no
            "complete": False,  # sentinel drained; not execution finish
            "finished": False,
            "retention_s": None,  # seconds, sampled once at finish
            "expire_at": None,  # monotonic deadline; None until finish
        }
    return token, output_queue


def note_execution_finished(token, output_queue, retention_ms):
    """Publish SENTINEL and start this job's retention clock.

    Both happen under the buffer lock, after the caller has restored
    the original streams. This does not take the history's reader lock:
    a reader may hold that lock while it waits for SENTINEL. A token
    already cleared is not put back; the sentinel is still published
    so a reader blocked on the old queue can wake.
    """
    interval = max(0.0, retention_ms / 1000.0)
    with _BUFFER_LOCK:
        output_queue.put(SENTINEL)
        entry = None if token is None else _BUFFER.get(token)
        if entry is None or entry["queue"] is not output_queue:
            return
        entry["finished"] = True
        entry["retention_s"] = interval
        entry["expire_at"] = time.monotonic() + interval


def _expired_result():
    """Tool result for an unknown or expired token."""
    return {
        "has_more": False,
        "error": "unknown or expired job_token",
        "page": [],
    }


def _out_of_range():
    """Tool result for a page_no this history will not serve."""
    return {
        "has_more": False,
        "error": "page_no out of range",
        "page": [],
    }


def _admit(token, entry, page_no):
    """Re-check identity and deadline, then extend a valid cursor.

    Caller holds the history's reader lock. Returns ("replay", None),
    ("forward", None), or ("reject", result). Extension and the
    deadline check share the buffer lock with the sweep.
    """
    with _BUFFER_LOCK:
        if _BUFFER.get(token) is not entry:
            return "reject", _expired_result()
        now = time.monotonic()
        expire_at = entry["expire_at"]
        if expire_at is not None and now >= expire_at:
            return "reject", _expired_result()
        max_no = len(entry["history"]) - 1
        if 0 <= page_no <= max_no:
            kind = "replay"
        elif page_no == max_no + 1 and not entry["complete"]:
            kind = "forward"
        else:
            return "reject", _out_of_range()
        if entry["finished"]:
            entry["expire_at"] = now + entry["retention_s"]
        return kind, None


def fetch(token, page_no, timeout_ms, page_size_chars):
    """Return the page at `page_no` from the job's history.

    Drains the live queue when `page_no` is the next page and page
    delivery is not yet complete. `page_size_chars` is the maximum
    serialized tool-result length.

    Result: {has_more, [page_no], [job_token], [error], page}, page last.
    A stored page carries page_no and job_token. An unstored empty
    non-final response carries job_token and no page_no. An unknown
    or expired token, or an out-of-range page_no, yields error.
    """
    _sweep_expired()
    with _BUFFER_LOCK:
        entry = _BUFFER.get(token)
    if entry is None:
        return _expired_result()

    with entry["lock"]:  # one reader at a time
        kind, rejected = _admit(token, entry, page_no)
        if rejected is not None:
            return rejected
        history = entry["history"]
        if kind == "replay":
            snap = history[page_no]
            return {
                "has_more": snap["has_more"],
                "page_no": page_no,
                "job_token": token,
                "page": snap["page"],
            }
        page, done = _drain(
            entry, timeout_ms, page_size_chars, page_no, token
        )
        if page or done:
            history.append(
                {"page_no": page_no, "page": page, "has_more": not done}
            )
            if done:
                entry["complete"] = True
            return {
                "has_more": not done,
                "page_no": page_no,
                "job_token": token,
                "page": page,
            }
        # Empty non-final drain: not stored, no page_no.
        return {"has_more": True, "job_token": token, "page": []}


def _json_text_len(text):
    """Characters `text` adds inside a JSON string."""
    return len(json.dumps(text)) - 2


def _addition_cost(page, stream, text):
    """Characters one write adds to the serialized tool result."""
    if page and page[-1]["stream"] == stream:
        return _json_text_len(text)
    obj = len(json.dumps({"stream": stream, "text": text}))
    if page:
        return obj + 2
    return obj


def measured_page_length(page, page_no, job_token):
    """Serialized length of `page`, summed one entry at a time.

    Equals ``serialized_page_length`` for the same page.
    """
    size = serialized_page_length([], page_no, job_token)
    built = []
    for item in page:
        size += _addition_cost(built, item["stream"], item["text"])
        built.append({"stream": item["stream"], "text": item["text"]})
    return size


def _commit(page, stream, text, size):
    """Append `text` and return the new serialized length."""
    size += _addition_cost(page, stream, text)
    if page and page[-1]["stream"] == stream:
        page[-1]["text"] += text
    else:
        page.append({"stream": stream, "text": text})
    return size


def _prefix_that_fits(page, stream, text, size, cap):
    """How many leading characters of `text` stay within `cap`."""
    if not text:
        return 0
    if size + _addition_cost(page, stream, text) <= cap:
        return len(text)
    lo, hi = 1, len(text)
    best = 0
    while lo <= hi:
        mid = (lo + hi) // 2
        cost = _addition_cost(page, stream, text[:mid])
        if size + cost <= cap:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def _cut_at_last_newline(page):
    """End `page` at its last newline. Return the text that followed it."""
    for index in range(len(page) - 1, -1, -1):
        newline = page[index]["text"].rfind("\n")
        if newline == -1:
            continue
        carried = []
        tail = page[index]["text"][newline + 1:]
        page[index]["text"] = page[index]["text"][: newline + 1]
        if tail:
            carried.append((page[index]["stream"], tail))
        for later in page[index + 1:]:
            carried.append((later["stream"], later["text"]))
        del page[index + 1:]
        return carried
    return None


def _hold(entry, pieces):
    """Put `pieces` back, in front of anything already waiting."""
    entry["pending"] = pieces + entry["pending"]


def _drain(entry, timeout_ms, page_size_chars, page_no, job_token):
    """Fill one page from the entry's queue.

    The page closes at `page_size_chars` of serialized tool result, at
    `timeout_ms`, or at the sentinel. A write that would pass the cap
    ends the page at the newline already on it. Returns (page, done);
    done means the sentinel was seen.
    """
    deadline = time.monotonic() + max(0.0, timeout_ms / 1000.0)
    output_queue = entry["queue"]
    page = []
    done = False
    size = serialized_page_length([], page_no, job_token)

    while True:
        if entry["pending"]:
            stream, piece = entry["pending"].pop(0)
        else:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                item = output_queue.get(timeout=remaining)
            except queue.Empty:
                break
            if item is SENTINEL:
                done = True
                break
            stream, piece = item

        if not piece:
            continue
        fit = _prefix_that_fits(
            page, stream, piece, size, page_size_chars
        )
        if fit == len(piece):
            size = _commit(page, stream, piece, size)
            continue
        if fit > 0:
            newline = piece[:fit].rfind("\n")
            if newline != -1:
                take = newline + 1
                size = _commit(page, stream, piece[:take], size)
                if piece[take:]:
                    _hold(entry, [(stream, piece[take:])])
                break
        carried = _cut_at_last_newline(page)
        if carried is not None:
            _hold(entry, carried + [(stream, piece)])
            size = serialized_page_length(page, page_no, job_token)
            break
        if fit > 0:
            size = _commit(page, stream, piece[:fit], size)
            if piece[fit:]:
                _hold(entry, [(stream, piece[fit:])])
            break
        if not page:
            # The cap cannot hold the result's fixed JSON plus one
            # character. Take one so this drain cannot sit still.
            size = _commit(page, stream, piece[:1], size)
            if len(piece) > 1:
                _hold(entry, [(stream, piece[1:])])
                break
            continue
        _hold(entry, [(stream, piece)])
        break

    return page, done


def _sweep_expired():
    """Delete histories whose retention deadline has passed."""
    now = time.monotonic()
    with _BUFFER_LOCK:
        expired = [
            token
            for token, entry in _BUFFER.items()
            if entry["expire_at"] is not None and now >= entry["expire_at"]
        ]
        for token in expired:
            del _BUFFER[token]


def clear_all():
    with _BUFFER_LOCK:
        _BUFFER.clear()


class MaintenanceHTTPServer(ThreadingHTTPServer):
    """Serve HTTP, and drop expired histories from the same loop.

    ``serve_forever`` already calls ``service_actions`` each pass. No
    extra thread or timer. A reader that was admitted keeps the entry
    it borrowed until that fetch returns.
    """

    allow_reuse_address = sys.platform != "win32"

    def server_bind(self):
        if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(
                socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1
            )
        super().server_bind()

    def service_actions(self):
        _sweep_expired()
        super().service_actions()
