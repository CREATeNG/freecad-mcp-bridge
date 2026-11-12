"""Each job the executor runs is one undo step.

The job runs inside an application-wide transaction, "MCP Bridge: run",
opened before the code and closed after, never aborted. A document
observer reopens it as "MCP Bridge: run, continued" whenever the code
commits or aborts a transaction of its own, and is removed before the
job's transaction is closed. These tests run executor.Executor._run
against stand-ins for FreeCAD and Qt, which record the calls in order.
"""

import importlib
import queue
import sys
import types
import unittest
from unittest import mock

RUN = "MCP Bridge: run"
CONT = "MCP Bridge: run, continued"


class StandInApp(types.ModuleType):
    """FreeCAD's transaction and observer calls, recorded in order.

    As in FreeCAD, a transaction that closes calls each registered
    observer's slotCommitTransaction(doc) or slotAbortTransaction(doc),
    then its slotCloseTransaction(abort), each only where the observer
    has it.
    """

    def __init__(self, with_calls=True, refuse_open=False):
        super().__init__("FreeCAD")
        self.log = []
        self.observers = []
        self.warnings = []
        self._refuse_open = refuse_open
        self.Console = types.SimpleNamespace(
            PrintMessage=lambda _text: None,
            PrintWarning=self.warnings.append,
        )
        if with_calls:
            self.setActiveTransaction = self._set_active
            self.closeActiveTransaction = self._close_active
            self.addDocumentObserver = self._add_observer
            self.removeDocumentObserver = self._remove_observer

    def _set_active(self, name, *args):
        if self._refuse_open:
            raise RuntimeError("no transactions here")
        self.log.append(("open", name) + args)

    def _close_active(self, *args, **kwargs):
        self.log.append(("close", args, kwargs))
        self._notify_close(bool(args[0] if args else kwargs.get("abort")))

    def _add_observer(self, observer):
        self.observers.append(observer)
        self.log.append(("add observer",))

    def _remove_observer(self, observer):
        self.observers.remove(observer)
        self.log.append(("remove observer",))

    def _notify_close(self, abort):
        doc = types.SimpleNamespace(Name="Doc")
        per_document = "slotAbortTransaction" if abort else "slotCommitTransaction"
        for observer in list(self.observers):
            if hasattr(observer, per_document):
                getattr(observer, per_document)(doc)
        for observer in list(self.observers):
            if hasattr(observer, "slotCloseTransaction"):
                observer.slotCloseTransaction(abort)

    def code_commits_its_own(self):
        """What the job's code does with doc.commitTransaction()."""
        self.log.append(("code commits",))
        self._notify_close(False)

    def code_aborts_its_own(self):
        """What the job's code does with doc.abortTransaction()."""
        self.log.append(("code aborts",))
        self._notify_close(True)


def load_executor(app):
    """executor.py imported against `app` and stand-ins for Qt."""
    qtcore = types.ModuleType("PySide.QtCore")

    class Signal:
        def __init__(self, *args):
            pass

        def connect(self, *args):
            pass

    qtcore.Signal = Signal
    qtcore.QObject = type("QObject", (), {"__init__": lambda self: None})
    qtcore.Qt = types.SimpleNamespace(QueuedConnection=0)
    qtcore.QCoreApplication = types.SimpleNamespace(instance=lambda: None)
    qtcore.QThread = types.SimpleNamespace(currentThread=lambda: None)
    pyside = types.ModuleType("PySide")
    pyside.QtCore = qtcore
    config = types.SimpleNamespace(max_history_retention_ms=lambda: 1000)
    paging = types.SimpleNamespace(note_execution_finished=lambda *a: None)
    import freecad.mcp_bridge as package

    patches = [
        mock.patch.dict(
            sys.modules,
            {
                "FreeCAD": app,
                "FreeCADGui": types.ModuleType("FreeCADGui"),
                "PySide": pyside,
                "PySide.QtCore": qtcore,
                "freecad.mcp_bridge.config": config,
                "freecad.mcp_bridge.paging": paging,
            },
        ),
        mock.patch.object(package, "config", config, create=True),
        mock.patch.object(package, "paging", paging, create=True),
    ]
    for patch in patches:
        patch.start()
    try:
        sys.modules.pop("freecad.mcp_bridge.executor", None)
        executor = importlib.import_module("freecad.mcp_bridge.executor")
    finally:
        sys.modules.pop("freecad.mcp_bridge.executor", None)
    return executor, patches


class UndoStepTests(unittest.TestCase):
    def run_job(self, code, app=None):
        """Run `code` as one job. Return the app and what stderr got."""
        app = app if app is not None else StandInApp()
        executor, patches = load_executor(app)
        for patch in patches:
            self.addCleanup(patch.stop)
        output = queue.Queue()
        executor.Executor._run(object(), code, output)
        stderr = "".join(
            text for stream, text in list(output.queue) if stream == "stderr"
        )
        return app, stderr

    def test_the_transaction_opens_before_the_code_and_closes_after(self):
        """One step named "MCP Bridge: run" opens before and closes after."""
        app, stderr = self.run_job("App.log.append(('code',))")
        self.assertEqual(
            app.log,
            [
                ("open", RUN),
                ("add observer",),
                ("code",),
                ("remove observer",),
                ("close", (), {}),
            ],
        )
        self.assertEqual(stderr, "")
        self.assertEqual(app.observers, [])
        self.assertEqual(app.warnings, [])

    def test_a_failed_job_keeps_what_ran(self):
        """An exception closes the step without aborting it."""
        app, stderr = self.run_job(
            "App.log.append(('code',))\nraise ValueError('boom')"
        )
        self.assertEqual(
            app.log,
            [
                ("open", RUN),
                ("add observer",),
                ("code",),
                ("remove observer",),
                ("close", (), {}),
            ],
        )
        self.assertIn("ValueError: boom", stderr)
        self.assertEqual(app.observers, [])

    def test_sys_exit_keeps_what_ran(self):
        """sys.exit in the code closes the step without aborting it."""
        app, stderr = self.run_job(
            "import sys\nApp.log.append(('code',))\nsys.exit(3)"
        )
        self.assertEqual(
            app.log,
            [
                ("open", RUN),
                ("add observer",),
                ("code",),
                ("remove observer",),
                ("close", (), {}),
            ],
        )
        self.assertIn("SystemExit: 3", stderr)
        self.assertEqual(app.observers, [])

    def test_a_commit_by_the_code_reopens_ours(self):
        """After the code's own commit, "MCP Bridge: run, continued" opens."""
        app, _ = self.run_job(
            "App.code_commits_its_own()\nApp.log.append(('code after',))"
        )
        self.assertEqual(
            app.log,
            [
                ("open", RUN),
                ("add observer",),
                ("code commits",),
                ("open", CONT),
                ("code after",),
                ("remove observer",),
                ("close", (), {}),
            ],
        )

    def test_an_abort_by_the_code_reopens_ours(self):
        """After the code's own abort, "MCP Bridge: run, continued" opens."""
        app, _ = self.run_job(
            "App.code_aborts_its_own()\nApp.log.append(('code after',))"
        )
        self.assertEqual(
            app.log,
            [
                ("open", RUN),
                ("add observer",),
                ("code aborts",),
                ("open", CONT),
                ("code after",),
                ("remove observer",),
                ("close", (), {}),
            ],
        )

    def test_closing_ours_opens_nothing_after_the_job(self):
        """The observer is gone before ours closes, so no step is left
        open after the job, even when removing it fails."""
        app = StandInApp()

        def refuse_removal(_observer):
            raise RuntimeError("cannot remove")

        app.removeDocumentObserver = refuse_removal
        app, _ = self.run_job("App.log.append(('code',))", app)
        self.assertEqual(
            app.log,
            [
                ("open", RUN),
                ("add observer",),
                ("code",),
                ("close", (), {}),
            ],
        )
        self.assertEqual(len(app.warnings), 1)
        self.assertIn("cannot remove", app.warnings[0])

    def test_a_freecad_without_the_calls_runs_the_job_as_before(self):
        """With no transaction calls the job runs, and nothing warns."""
        app = StandInApp(with_calls=False)
        app, stderr = self.run_job("App.log.append(('code',))", app)
        self.assertEqual(app.log, [("code",)])
        self.assertEqual(stderr, "")
        self.assertEqual(app.warnings, [])

    def test_a_refused_transaction_still_runs_the_job_and_warns(self):
        """If the step can't open, the job runs, nothing is closed, and
        the Report view says the run may not be one undo step."""
        app = StandInApp(refuse_open=True)
        app, stderr = self.run_job("App.log.append(('code',))", app)
        self.assertEqual(app.log, [("code",)])
        self.assertEqual(stderr, "")
        self.assertEqual(
            app.warnings,
            [
                "[MCP Bridge] This run may not be one step in Edit → Undo "
                "(no transactions here).\n"
            ],
        )


if __name__ == "__main__":
    unittest.main()
