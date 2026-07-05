"""Qt main-thread execution of user code.

Only the Qt main thread may touch FreeCAD. The HTTP server runs off-thread,
so Executor bridges the gap: submit() puts the job on a FIFO job queue and
emits an argument-less wake-up signal; the dispatcher slot runs on the main
thread and drains the queue one job at a time, each job running to
completion before the next is taken. A busy latch makes nested dispatcher
invocations no-ops — executed code that pumps the Qt event loop would
otherwise have the next queued exec delivered nested inside the current one.
Output is teed to the page-buffer queue and FreeCAD's Report View as it is
produced.
"""

import queue
import sys
import traceback

import FreeCAD
from PySide.QtCore import QObject, Qt, Signal

from freecad.mcp_bridge.paging import SENTINEL


class TeeWriter:
    """File-like sink: every write goes to the page queue and the Report View."""

    def __init__(self, output_queue):
        self._queue = output_queue

    def write(self, text):
        if text:
            self._queue.put(text)
            FreeCAD.Console.PrintMessage(text)

    def flush(self):
        pass


class Executor(QObject):
    # Pure wake-up: carries no job data. Jobs travel only through _jobs.
    wake_up = Signal()

    def __init__(self):
        super().__init__()
        self._jobs = queue.Queue()  # (code, output_queue), FIFO
        self._busy = False  # main-thread only: True while a job is running
        self.wake_up.connect(self._dispatch, Qt.QueuedConnection)

    def submit(self, code, output_queue):
        """Queue the job and wake the dispatcher. Called from a request thread."""
        self._jobs.put((code, output_queue))
        self.wake_up.emit()

    def _dispatch(self):
        """Drain the job queue on the main thread, strictly one job at a time.

        A wake-up delivered while a job is running (its code pumped the Qt
        event loop) finds the latch set and returns without touching the
        queue; the suspended outer loop takes that job when the current one
        returns. A wake-up that arrives after the loop already drained its
        job finds the queue empty and is a harmless no-op.
        """
        if self._busy:
            return
        self._busy = True
        try:
            while True:
                try:
                    code, output_queue = self._jobs.get_nowait()
                except queue.Empty:
                    break
                self._run(code, output_queue)
        finally:
            self._busy = False

    def _run(self, code, output_queue):
        """Run one job to completion: exec, traceback on error, sentinel."""
        import FreeCADGui

        tee = TeeWriter(output_queue)
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = tee, tee
        env = {
            "FreeCAD": FreeCAD,
            "App": FreeCAD,
            "FreeCADGui": FreeCADGui,
            "Gui": FreeCADGui,
        }
        try:
            exec(code, env)
        except Exception:
            tee.write("\n" + traceback.format_exc())
        finally:
            sys.stdout, sys.stderr = old_out, old_err
            output_queue.put(SENTINEL)
