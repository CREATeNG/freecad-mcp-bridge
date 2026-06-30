"""Qt main-thread execution of user code.

Only the Qt main thread may touch FreeCAD. The HTTP server runs off-thread, so
Executor (a QObject with main-thread affinity) bridges the gap: submit() is
called from a request thread and emits a queued signal, so _run executes on the
main thread. Output is teed to the page-buffer queue and FreeCAD's Report View
as it is produced. Queued delivery serializes execs — one runs at a time.
"""

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
    exec_requested = Signal(str, object)  # (code, output_queue)

    def __init__(self):
        super().__init__()
        self.exec_requested.connect(self._run, Qt.QueuedConnection)

    def submit(self, code, output_queue):
        """Dispatch exec to the main thread. Called from a request thread."""
        self.exec_requested.emit(code, output_queue)

    def _run(self, code, output_queue):
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
