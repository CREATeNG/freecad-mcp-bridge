"""Qt main-thread execution of user code.

Only the Qt main thread may touch FreeCAD. The HTTP server runs off-thread,
so Executor bridges the gap: submit() puts the job on a FIFO job queue and
emits an argument-less wake-up signal; the dispatcher slot runs on the main
thread and drains the queue one job at a time, each job running to
completion before the next is taken. A busy latch makes nested dispatcher
invocations no-ops — executed code that pumps the Qt event loop would
otherwise have the next queued exec delivered nested inside the current one.
Stopping the bridge shuts the executor down: jobs still in the queue are
dropped and never run; a job already running finishes.
Output is teed to the page-buffer queue and FreeCAD's Report View as it is
produced.
"""

import sys
import threading
import traceback

import FreeCAD
from PySide.QtCore import QCoreApplication, QObject, Qt, QThread, Signal

from freecad.mcp_bridge import config, paging
from freecad.mcp_bridge.job_gate import JobGate, JobLine


class TeeWriter:
    """File-like sink: every write goes to the page queue and the Report View."""

    def __init__(self, output_queue, stream):
        self._queue = output_queue
        self._stream = stream

    def write(self, text):
        if text:
            self._queue.put((self._stream, text))
            FreeCAD.Console.PrintMessage(text)

    def flush(self):
        pass


class Executor(QObject):
    # Pure wake-up: carries no job data. Jobs travel only through the gate.
    wake_up = Signal()
    ask = Signal(object)

    def __init__(self):
        super().__init__()
        # (code, output_queue, filepath, token), FIFO
        self._gate = JobGate()
        # Acceptance scripts count jobs still waiting on this object.
        self._jobs = JobLine(self._gate)
        self._busy = False  # main-thread only: True while a job is running
        self._stopped = False  # set by shutdown(); no further job is taken
        self.wake_up.connect(self._dispatch, Qt.QueuedConnection)
        self.ask.connect(self._run_ask, Qt.QueuedConnection)

    def file_turn(self):
        """Hold the job queue for one file dialog, then wake the dispatcher."""
        gate = self._gate

        class _Turn:
            def __enter__(self_turn):
                self_turn._ctx = gate.file_turn()
                return self_turn._ctx.__enter__()

            def __exit__(self_turn, exc_type, exc, tb):
                result = self_turn._ctx.__exit__(exc_type, exc, tb)
                self.wake_up.emit()
                return result

        return _Turn()

    def call_on_main(self, fn):
        """Run `fn` on the main thread and return its result.

        The caller, on a request thread, waits. A modal dialog inside
        `fn` runs its own loop on the main thread, so this wait is not
        the response timeout.
        """
        app = QCoreApplication.instance()
        if app is not None and QThread.currentThread() is app.thread():
            return fn()
        done = threading.Event()
        box = {}

        def wrapped():
            try:
                box["value"] = fn()
            except BaseException as exc:
                box["error"] = exc
            finally:
                done.set()

        self.ask.emit(wrapped)
        done.wait()
        if "error" in box:
            raise box["error"]
        return box.get("value")

    def _run_ask(self, fn):
        fn()

    def submit(self, code, output_queue, filepath=None, token=None):
        """Queue the job and wake the dispatcher. Called from a request thread."""
        if self._stopped:
            return
        job = (code, output_queue, filepath, token)
        if self._gate.submit(job):
            self.wake_up.emit()

    def _dispatch(self):
        """Drain the job queue on the main thread, strictly one job at a time.

        A wake-up delivered while a job is running (its code pumped the Qt
        event loop) finds the latch set and returns without touching the
        queue; the suspended outer loop takes that job when the current one
        returns. A wake-up while a file dialog is holding the queue returns
        the same way. A wake-up that arrives after the loop already drained
        its job finds the queue empty and is a harmless no-op.
        """
        if self._busy:
            return
        self._busy = True
        try:
            while not self._stopped:
                job = self._gate.pop()
                if job is None:
                    break
                code, output_queue, filepath, token = job
                self._run(code, output_queue, filepath, token)
        finally:
            self._busy = False

    def shutdown(self):
        """Take no further job, and drop those still queued; they never run.

        A job already running finishes. Called on the main thread when the
        bridge stops.
        """
        self._stopped = True
        self._gate.clear()

    def _run(self, code, output_queue, filepath=None, token=None):
        """Run one job to completion: exec, traceback on error, sentinel."""
        import FreeCADGui

        stdout_tee = TeeWriter(output_queue, "stdout")
        stderr_tee = TeeWriter(output_queue, "stderr")
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = stdout_tee, stderr_tee
        env = {
            # As FreeCAD's macro runner does, so a script's
            # `if __name__ == "__main__":` block runs.
            "__name__": "__main__",
            "FreeCAD": FreeCAD,
            "App": FreeCAD,
            "FreeCADGui": FreeCADGui,
            "Gui": FreeCADGui,
        }
        if filepath is not None:
            env["__file__"] = filepath
        try:
            exec(code, env)
        except BaseException:
            # BaseException, so a job's sys.exit() ends that job like any
            # other error instead of escaping and stalling the queue.
            stderr_tee.write("\n" + traceback.format_exc())
        finally:
            sys.stdout, sys.stderr = old_out, old_err
            paging.note_execution_finished(
                token,
                output_queue,
                config.max_history_retention_ms(),
            )
