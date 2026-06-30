"""Page buffer for streamed exec output.

Each execute call gets a buffer entry keyed by a page_token: a queue the Qt
main thread pushes output chunks (then SENTINEL) onto, and a per-entry lock so
only one reader drains it at a time. Pure stdlib — no FreeCAD/Qt — so it stays
unit-testable on plain CPython.
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
        _BUFFER[token] = {"queue": output_queue, "lock": threading.Lock()}
    return token, output_queue


def drain(token, timeout_ms):
    """Collect output for `token`, waiting up to `timeout_ms` for the sentinel.

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
    done = False

    with entry["lock"]:  # FIFO — one reader at a time
        output_queue = entry["queue"]
        while True:
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
            chunks.append(item)

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
