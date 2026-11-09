"""FIFO job queue that can pause while a file dialog is open.

Jobs submitted before the dialog stay ahead of the file. Jobs submitted
while the dialog is up run after that file. A second file turn waits
until the first has released the queue.
"""

from __future__ import annotations

import threading
from collections import deque
from contextlib import contextmanager


class FileTurn:
    """One file dialog's place in the queue."""

    def __init__(self):
        self.job = None

    def queue(self, job) -> None:
        """Run `job` after earlier jobs, ahead of ones sent during it."""
        self.job = job


class JobGate:
    """Hold and release the execution queue around a file dialog."""

    def __init__(self):
        self._lock = threading.Lock()
        self._turn = threading.Lock()
        self._jobs = deque()
        self._deferred = deque()
        self._holding = False

    def submit(self, job) -> bool:
        """Queue `job`. Return True when the dispatcher should wake."""
        with self._lock:
            if self._holding:
                self._deferred.append(job)
                return False
            self._jobs.append(job)
            return True

    def pop(self):
        """Return the next job, or None while held or empty."""
        with self._lock:
            if self._holding or not self._jobs:
                return None
            return self._jobs.popleft()

    def clear(self) -> None:
        """Drop queued and deferred jobs."""
        with self._lock:
            self._jobs.clear()
            self._deferred.clear()

    def waiting(self) -> int:
        """How many jobs are queued and have not been taken yet."""
        with self._lock:
            return len(self._jobs) + len(self._deferred)

    @contextmanager
    def file_turn(self):
        """Hold the queue until the turn exits. One turn at a time."""
        self._turn.acquire()
        turn = FileTurn()
        with self._lock:
            self._holding = True
        try:
            yield turn
        finally:
            with self._lock:
                if turn.job is not None:
                    self._jobs.append(turn.job)
                self._jobs.extend(self._deferred)
                self._deferred.clear()
                self._holding = False
            self._turn.release()


class JobLine:
    """Jobs still waiting. Acceptance scripts read qsize and empty."""

    def __init__(self, gate: JobGate):
        self._gate = gate

    def qsize(self) -> int:
        return self._gate.waiting()

    def empty(self) -> bool:
        return self.qsize() == 0
