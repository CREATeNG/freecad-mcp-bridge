"""Page buffer for streamed exec output.

Each execute call gets a buffer entry keyed by a page_token: a queue the Qt
main thread pushes output chunks (then SENTINEL) onto, and a per-entry lock so
only one reader drains it at a time. drain() bounds a single page by both
time (timeout_ms) and size (page_size_chars) — a chunk that doesn't fully fit
a page is held in "pending" and finished off on the next call, without
re-copying what's left of it each time.
"""

import queue
import threading
import time
import uuid

# End-of-output marker placed on a page's queue by the executor.
SENTINEL = object()

_BUFFER = {}
_BUFFER_LOCK = threading.Lock()


def start_page():
    """Create a new buffer entry; return (page_token, output_queue)."""
    token = uuid.uuid4().hex
    output_queue = queue.Queue()
    with _BUFFER_LOCK:
        _BUFFER[token] = {
            "queue": output_queue,
            "lock": threading.Lock(),
            # (text, offset) for a chunk that didn't fully fit in a page —
            # checked before pulling a new item, so a chunk can be split
            # across pages without re-copying its untaken remainder each time.
            "pending": None,
        }
    return token, output_queue


def drain(token, timeout_ms, page_size_chars):
    """Collect up to `page_size_chars` of output for `token`, waiting up to
    `timeout_ms` for the sentinel or enough output to fill a page.

    Returns {output, has_more}; while has_more, also the page_token to poll
    with. An unknown/expired token yields has_more=False plus an error string.
    """
    with _BUFFER_LOCK:
        entry = _BUFFER.get(token)
    if entry is None:
        return {
            "output": "",
            "has_more": False,
            "error": "unknown or expired page_token",
        }

    deadline = time.monotonic() + max(0.0, timeout_ms / 1000.0)
    chunks = []
    total = 0
    done = False

    with entry["lock"]:  # FIFO — one reader at a time
        output_queue = entry["queue"]
        while total < page_size_chars:
            if entry["pending"] is not None:
                text, offset = entry["pending"]
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
                text, offset = item, 0

            room = page_size_chars - total
            available = len(text) - offset
            if available <= room:
                chunks.append(text[offset:])
                total += available
            else:
                chunks.append(text[offset:offset + room])
                total += room
                entry["pending"] = (text, offset + room)
                break

        # The page filled exactly, and we don't yet know if that was also the
        # end of the stream. Check once, without waiting: if the sentinel (or
        # more data) is already queued, resolve it now instead of forcing a
        # round trip that would just find it next time. If nothing's ready
        # yet, has_more stays True — we don't block for an answer here.
        if total >= page_size_chars and not done and entry["pending"] is None:
            try:
                item = output_queue.get_nowait()
            except queue.Empty:
                pass
            else:
                if item is SENTINEL:
                    done = True
                else:
                    entry["pending"] = (item, 0)

    output = "".join(chunks)
    if done:
        evict(token)
        return {"output": output, "has_more": False}
    return {"output": output, "has_more": True, "page_token": token}


def evict(token):
    with _BUFFER_LOCK:
        _BUFFER.pop(token, None)


def clear_all():
    with _BUFFER_LOCK:
        _BUFFER.clear()
