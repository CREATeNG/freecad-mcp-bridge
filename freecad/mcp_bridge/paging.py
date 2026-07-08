"""Page history for streamed job output.

Each job gets a history entry keyed by a job_token: an output queue the Qt
main thread pushes (stream, text) chunks (then SENTINEL) onto, a per-entry
lock so only one reader drains at a time, and an append-only list of stored
page snapshots. fetch() returns the page at a given page_no, draining the
live queue forward when that page is the next one and the job is still
running. A page is bounded by both time (timeout_ms) and size
(page_size_chars); a chunk that doesn't fully fit is held in "pending" and
continued on the next page, without re-copying its remainder each time.

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
        if page_no <= max_no:
            snap = history[page_no]
            return {
                "page": snap["page"],
                "page_no": page_no,
                "has_more": snap["has_more"],
                "job_token": token,
            }

        # Next page, job still running — drain the live queue forward.
        if page_no == max_no + 1 and not entry["complete"]:
            page, done = _drain(entry, timeout_ms, page_size_chars)
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


def _drain(entry, timeout_ms, page_size_chars):
    """Fill one page from the entry's queue, up to page_size_chars, waiting up
    to timeout_ms. Returns (page, done) where done means the SENTINEL was seen.
    """
    deadline = time.monotonic() + max(0.0, timeout_ms / 1000.0)
    output_queue = entry["queue"]
    page = []
    total = 0
    done = False

    while total < page_size_chars:
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

        room = page_size_chars - total
        available = len(text) - offset
        if available <= room:
            page.append({"stream": stream, "text": text[offset:]})
            total += available
        else:
            page.append({"stream": stream, "text": text[offset:offset + room]})
            total += room
            entry["pending"] = (stream, text, offset + room)
            break

    # The page filled exactly, and we don't yet know if that was also the end
    # of the stream. Check once, without waiting: if the sentinel (or more
    # data) is already queued, resolve it now instead of forcing a round trip
    # that would just find it next time.
    if total >= page_size_chars and not done and entry["pending"] is None:
        try:
            item = output_queue.get_nowait()
        except queue.Empty:
            pass
        else:
            if item is SENTINEL:
                done = True
            else:
                stream, text = item
                entry["pending"] = (stream, text, 0)

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
