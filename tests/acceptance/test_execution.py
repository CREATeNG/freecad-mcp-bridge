"""Requirements on running Python inside the live FreeCAD session.

Each test name, and the first line of its docstring, states one requirement
as an agent running code through the bridge experiences it, against a real
bridge in a real FreeCAD.
"""

import os
import tempfile
import threading
import time
import unittest

from tests.acceptance import mcp_client as mcp

# Prints a marker, then, still running, waits for FreeCAD's Report view to
# show it, processing GUI events so the view can update.
REPORT_VIEW_JOB = """
import time
import uuid
from PySide.QtCore import QCoreApplication
from PySide.QtWidgets import QDockWidget, QPlainTextEdit, QTextEdit


def report_view_text():
    dock = Gui.getMainWindow().findChild(QDockWidget, 'Report view')
    if dock is None:
        return ''
    dock.show()
    for kind in (QTextEdit, QPlainTextEdit):
        for view in dock.findChildren(kind):
            return view.toPlainText()
    return ''


marker = f'report-view-{uuid.uuid4().hex}'
print(marker)
seen = False
deadline = time.monotonic() + 5
while not seen and time.monotonic() < deadline:
    QCoreApplication.processEvents()
    seen = marker in report_view_text()
    time.sleep(0.05)
print('in the Report view while running:', seen)
"""

# Counts the paint events FreeCAD's widgets receive while the script asks
# for a repaint and refreshes the GUI, as a long macro does, then carries on.
REFRESHING_JOB = """
import time
from PySide.QtCore import QCoreApplication, QEvent, QObject


class PaintCounter(QObject):
    def __init__(self):
        super().__init__()
        self.count = 0

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Paint:
            self.count += 1
        return False


app = QCoreApplication.instance()
counter = PaintCounter()
app.installEventFilter(counter)
try:
    Gui.getMainWindow().update()
    deadline = time.monotonic() + 5
    while counter.count == 0 and time.monotonic() < deadline:
        Gui.updateGui()
        time.sleep(0.05)
finally:
    app.removeEventFilter(counter)
print('repainted:', counter.count > 0)
print('still running')
"""


def pumping_job(log):
    """Job A: log its start, process GUI events until job B is in line
    behind it and for a second more, as a script that keeps the GUI
    responsive does, then log its end."""
    return f"""
import time
from PySide.QtCore import QCoreApplication
from freecad.mcp_bridge import bridge


def log(line):
    with open({log!r}, 'a') as f:
        f.write(line + '\\n')


def pump(seconds, until=lambda: False):
    deadline = time.monotonic() + seconds
    while not until() and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.05)


log('A start')
# Precondition only: no public view of the job queue exists.
line = bridge._server._executor._jobs
pump(10, until=lambda: not line.empty())
print('queued behind:', line.qsize())
pump(1)
log('A end')
"""


class ExecutionContextTests(unittest.TestCase):
    def test_code_can_use_app_and_gui_without_importing_them_and_create_a_document_object(self):
        """Executed code can use App, FreeCAD, Gui and FreeCADGui without importing them, and can create a document object in the open session."""
        output = mcp.run(
            "print(App is FreeCAD, Gui is FreeCADGui, Gui.getMainWindow() is not None)\n"
            "doc = App.newDocument('AcceptanceDoc')\n"
            "doc.addObject('App::DocumentObjectGroup', 'AcceptanceGroup')\n"
            "doc.recompute()\n"
            "print(doc.Name)\n"
        )
        lines = output.splitlines()
        self.assertEqual(len(lines), 2, output)
        self.assertEqual(lines[0], "True True True")
        name = lines[1]
        try:
            # A separate job sees what the first one made: it lives in the session.
            found = mcp.run(
                f"print(App.getDocument({name!r}).getObject('AcceptanceGroup').TypeId)"
            )
            self.assertEqual(found, "App::DocumentObjectGroup\n")
        finally:
            mcp.run(f"App.closeDocument({name!r})")

    def test_a_scripts_main_block_runs(self):
        """A script's `if __name__ == "__main__":` block runs."""
        output = mcp.run('if __name__ == "__main__":\n    print("main block ran")\n')
        self.assertEqual(output, "main block ran\n")

    def test_executed_code_is_not_sandboxed(self):
        """Executed code is unsandboxed: it can import os and write a file."""
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "written.txt")
            output = mcp.run(
                "import os\n"
                f"with open({path!r}, 'w') as handle:\n"
                "    handle.write(str(os.getpid()))\n"
                "print(os.getpid())\n"
            )
            with open(path, encoding="utf-8") as handle:
                written = handle.read()
        self.assertEqual(output, f"{written}\n")


class ScriptFileTests(unittest.TestCase):
    def test_a_local_file_runs_in_the_same_context_and_sees_its_own_absolute_path(self):
        """execute_python_file runs a local .py file given by absolute path, in the same context as inline code, with __file__ set to that path."""
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.abspath(os.path.join(folder, "script.py"))
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(
                    "print(__file__)\n"
                    "print(App is FreeCAD, Gui is FreeCADGui, __name__)\n"
                )
            output = mcp.text(mcp.follow(mcp.execute_file(path)))
        self.assertEqual(output.splitlines(), [path, "True True __main__"])

    def test_a_file_that_cannot_be_read_returns_an_explanatory_error_and_no_job(self):
        """A file that cannot be read returns an empty final result with an explanatory error and no job token."""
        with tempfile.TemporaryDirectory() as folder:
            missing = os.path.join(folder, "missing.py")
            result = mcp.execute_file(missing)
        self.assertEqual(result["page"], [])
        self.assertFalse(result["has_more"])
        self.assertIn(missing, result["error"])
        self.assertNotIn("job_token", result)
        self.assertNotIn("page_no", result)


class OutputTests(unittest.TestCase):
    def test_printed_text_is_returned(self):
        """Text the code prints is returned to the agent."""
        self.assertEqual(mcp.run("print('hello from FreeCAD')"), "hello from FreeCAD\n")

    def test_stdout_and_stderr_chunks_keep_their_labels_and_order(self):
        """stdout and stderr output arrive as chunks that keep their stream labels and the order they were written in."""
        result = mcp.execute(
            "import sys\n"
            "sys.stdout.write('one')\n"
            "sys.stderr.write('two')\n"
            "sys.stdout.write('three')\n"
        )
        chunks = [c for r in mcp.follow(result) for c in r["page"]]
        self.assertEqual(
            chunks,
            [
                {"stream": "stdout", "text": "one"},
                {"stream": "stderr", "text": "two"},
                {"stream": "stdout", "text": "three"},
            ],
        )

    def test_an_exception_comes_back_as_a_traceback_on_stderr_not_as_a_tool_error(self):
        """A Python exception comes back as traceback text on stderr, not as a tool error."""
        results = mcp.follow(mcp.execute("raise ValueError('acceptance boom')"))
        for result in results:
            self.assertNotIn("error", result)
        stderr = "".join(
            c["text"] for r in results for c in r["page"] if c["stream"] == "stderr"
        )
        self.assertIn("Traceback", stderr)
        self.assertIn("ValueError: acceptance boom", stderr)

    def test_output_also_appears_in_the_report_view_as_it_is_produced(self):
        """Output also appears in FreeCAD's Report view as it is produced."""
        output = mcp.run(REPORT_VIEW_JOB)
        self.assertTrue(
            output.endswith("in the Report view while running: True\n"),
            output,
        )

    def test_the_next_script_runs_after_one_that_raised(self):
        """After a script raises, the next script still runs."""
        mcp.run("raise RuntimeError('first fails')")
        self.assertEqual(mcp.run("print('second runs')"), "second runs\n")


class OrderingTests(unittest.TestCase):
    def test_scripts_run_one_at_a_time_in_submission_order(self):
        """Scripts run one at a time, in the order they were submitted, even when submitted concurrently."""
        jobs = 4
        with tempfile.TemporaryDirectory() as folder:
            log = os.path.join(folder, "order.log")

            def code(n, seconds):
                return (
                    "import time\n"
                    f"with open({log!r}, 'a') as f: f.write('start {n}\\n')\n"
                    f"time.sleep({seconds})\n"
                    f"with open({log!r}, 'a') as f: f.write('end {n}\\n')\n"
                )

            # Job 0 holds the main thread long enough for the others to queue
            # behind it; each later job is sent from its own thread, staggered
            # so the order they reach the bridge is known.
            threads = []
            for n in range(jobs):
                thread = threading.Thread(
                    target=lambda n=n: mcp.run(code(n, 3 if n == 0 else 0.2))
                )
                thread.start()
                threads.append(thread)
                time.sleep(0.3)
            for thread in threads:
                thread.join(timeout=60)

            with open(log, encoding="utf-8") as handle:
                events = handle.read().splitlines()
        expected = [f"{what} {n}" for n in range(jobs) for what in ("start", "end")]
        self.assertEqual(events, expected)


class GuiEventTests(unittest.TestCase):
    def test_a_script_processing_gui_events_never_has_another_run_in_it(self):
        """A later script never runs inside one that processes GUI events."""
        with tempfile.TemporaryDirectory() as folder:
            log = os.path.join(folder, "order.log")
            outputs = {}

            def submit(key, code):
                outputs[key] = mcp.run(code)

            pumping = threading.Thread(
                target=submit, args=("a", pumping_job(log))
            )
            pumping.start()
            time.sleep(1)
            later = threading.Thread(
                target=submit,
                args=("b", f"open({log!r}, 'a').write('B\\n')"),
            )
            later.start()
            pumping.join(timeout=60)
            later.join(timeout=60)

            with open(log, encoding="utf-8") as handle:
                events = handle.read().splitlines()
        self.assertIn("queued behind: 1", outputs["a"])
        self.assertEqual(events, ["A start", "A end", "B"])

    def test_a_script_that_refreshes_the_gui_lets_it_repaint(self):
        """A script refreshing the GUI lets it repaint, and then continues."""
        output = mcp.run(REFRESHING_JOB)
        self.assertEqual(output, "repainted: True\nstill running\n")


if __name__ == "__main__":
    unittest.main()
