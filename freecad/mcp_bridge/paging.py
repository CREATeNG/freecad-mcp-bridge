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
an empty non-final drain is not stored and consumes no page number. A job is
complete once its SENTINEL has been drained into a page; that final page
carries has_more=False. Completed histories are retained for retention_ms,
then swept.

This module must be able to run outside FreeCAD.
"""

import queue
import threading
import time
import uuid

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
            # (stream, text, offset) for a chunk that didn't fully fit in a
            # page — checked before pulling a new item, so a chunk can be
            # split across pages without re-copying its untaken remainder.
            "pending": None,
            "history": [],  # stored page snapshots, indexed by page_no
            "complete": False,
            "expire_at": None,  # monotonic deadline, set once complete
        }
    return token, output_queue


def fetch(token, page_no, timeout_ms, page_size_chars, retention_ms):
    """Return the page at `page_no` from the job's history, draining the live
    queue forward if it is the next page and the job is still running.
    `page_size_chars` is the maximum serialized tool-result length.

    Result: {page, has_more}; plus `page_no` and `job_token` for a stored
    page, or `job_token` alone for an unstored empty non-final response. An
    unknown/expired token or out-of-range page_no yields an `error` string.
    """
    _sweep_expired()
    with _BUFFER_LOCK:
        entry = _BUFFER.get(token)
    if entry is None:
        return {
            "page": [],
            "has_more": False,
            "error": "unknown or expired job_token",
        }

    with entry["lock"]:  # one reader at a time
        history = entry["history"]
        max_no = len(history) - 1

        # Stored page — idempotent replay.
        if 0 <= page_no <= max_no:
            snap = history[page_no]
            return {
                "page": snap["page"],
                "page_no": page_no,
                "has_more": snap["has_more"],
                "job_token": token,
            }

        # Next page, job still running — drain the live queue forward.
        if page_no == max_no + 1 and not entry["complete"]:
            page, done = _drain(
                entry, timeout_ms, page_size_chars, page_no, token
            )
            if page or done:
                history.append(
                    {"page_no": page_no, "page": page, "has_more": not done}
                )
                if done:
                    entry["complete"] = True
                    entry["expire_at"] = time.monotonic() + max(
                        0.0, retention_ms / 1000.0
                    )
                return {
                    "page": page,
                    "page_no": page_no,
                    "has_more": not done,
                    "job_token": token,
                }
            # Empty non-final drain: not stored, no page_no.
            return {"page": [], "has_more": True, "job_token": token}

        return {
            "page": [],
            "has_more": False,
            "error": "page_no out of range",
        }


def _append_text(page, stream, text):
    """Join `text` onto the last entry when it is the same stream."""
    if page and page[-1]["stream"] == stream:
        page[-1]["text"] += text
    else:
        page.append({"stream": stream, "text": text})


def _longest_prefix(page, stream, text, page_no, job_token, cap):
    """How many leading characters of `text` keep the page within `cap`.

    `page` is not modified. Zero means even one character does not fit.
    """
    if not text:
        return 0
    trial = [dict(item) for item in page]
    _append_text(trial, stream, text)
    if serialized_page_length(trial, page_no, job_token) <= cap:
        return len(text)
    lo, hi = 1, len(text)
    best = 0
    while lo <= hi:
        mid = (lo + hi) // 2
        trial = [dict(item) for item in page]
        _append_text(trial, stream, text[:mid])
        if serialized_page_length(trial, page_no, job_token) <= cap:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def _drain(entry, timeout_ms, page_size_chars, page_no, job_token):
    """Fill one page from the entry's queue.

    The page closes at `page_size_chars` of serialized tool result, at
    `timeout_ms`, or at the sentinel. Returns (page, done); done means
    the sentinel was seen.
    """
    deadline = time.monotonic() + max(0.0, timeout_ms / 1000.0)
    output_queue = entry["queue"]
    page = []
    done = False

    while True:
        if entry["pending"] is not None:
            stream, text, offset = entry["pending"]
            entry["pending"] = None
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
            stream, text = item
            offset = 0

        piece = text[offset:]
        if not piece:
            continue
        fit = _longest_prefix(
            page, stream, piece, page_no, job_token, page_size_chars
        )
        if fit == len(piece):
            _append_text(page, stream, piece)
            continue
        if fit > 0:
            newline = piece[:fit].rfind("\n")
            if newline != -1:
                take = newline + 1
                _append_text(page, stream, piece[:take])
                entry["pending"] = (stream, text, offset + take)
                break
            if not page:
                _append_text(page, stream, piece[:fit])
                entry["pending"] = (stream, text, offset + fit)
                break
        elif not page:
            # The cap cannot hold the result's fixed JSON plus one
            # character. Take one so this drain cannot sit still.
            _append_text(page, stream, piece[:1])
            if len(piece) == 1:
                continue
            entry["pending"] = (stream, text, offset + 1)
            break
        entry["pending"] = (stream, text, offset)
        break

    return page, done


def _sweep_expired():
    """Delete completed histories whose retention period has elapsed."""
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
