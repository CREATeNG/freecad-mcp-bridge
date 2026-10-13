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


if __name__ == "__main__":
    unittest.main()
