"""Requirements on asking before an agent's code or file runs."""

import importlib
import sys
import threading
import time
import types
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

from freecad.mcp_bridge import paging
from freecad.mcp_bridge.file_prompt import (
    CODE_DECLINE,
    CODE_INTRO,
    DECLINE,
    MODE_BODY,
    RUN,
    UNRESTRICTED_BUTTON,
    commit_mode,
    decide,
    decline_message,
)
from freecad.mcp_bridge.resources import mode_icon_path
from freecad.mcp_bridge.job_gate import JobGate, JobLine


def _load_http_server():
    """Import the server with FreeCAD and the executor stubbed out."""
    fake_executor = types.ModuleType("freecad.mcp_bridge.executor")
    fake_executor.Executor = object
    with mock.patch.dict(
        sys.modules,
        {
            "FreeCAD": types.SimpleNamespace(),
            "freecad.mcp_bridge.executor": fake_executor,
        },
    ):
        sys.modules.pop("freecad.mcp_bridge.config", None)
        sys.modules.pop("freecad.mcp_bridge.http_server", None)
        return importlib.import_module("freecad.mcp_bridge.http_server")


class DecideTests(unittest.TestCase):
    def test_unrestricted_mode_skips_the_dialog(self):
        """In Unrestricted mode the code runs without asking."""
        asked = []
        self.assertEqual(
            decide(
                asked.append,
                unrestricted=True,
                go_unrestricted=lambda: None,
            ),
            RUN,
        )
        self.assertEqual(asked, [])

    def test_dont_run_is_a_decline_and_leaves_the_mode_at_ask(self):
        """Don't run tells the caller to decline and keeps Ask."""
        switched = []
        choice = decide(
            lambda _kind: "Don't run",
            unrestricted=False,
            go_unrestricted=lambda: switched.append(True),
        )
        self.assertEqual(choice, DECLINE)
        self.assertEqual(switched, [])
        self.assertIn("declined", decline_message("/tmp/a.py"))

    def test_run_returns_run_and_leaves_the_mode_at_ask(self):
        """Run runs the code and keeps Ask."""
        switched = []
        choice = decide(
            lambda _kind: "Run",
            unrestricted=False,
            go_unrestricted=lambda: switched.append(True),
        )
        self.assertEqual(choice, RUN)
        self.assertEqual(switched, [])

    def test_no_on_the_second_dialog_returns_to_the_first(self):
        """No on the Unrestricted confirm asks again and keeps Ask."""
        self.assertEqual(UNRESTRICTED_BUTTON, "Unrestricted!")
        answers = [UNRESTRICTED_BUTTON, "No", "Run"]
        asked = []
        switched = []

        def ask(kind):
            asked.append(kind)
            return answers.pop(0)

        choice = decide(
            ask,
            unrestricted=False,
            go_unrestricted=lambda: switched.append(True),
        )
        self.assertEqual(choice, RUN)
        self.assertEqual(asked, ["run", "mode", "run"])
        self.assertEqual(switched, [])

    def test_yes_on_the_second_dialog_goes_unrestricted_and_runs(self):
        """Yes switches to Unrestricted, and the code shown then runs."""
        answers = [UNRESTRICTED_BUTTON, "Yes"]
        switched = []
        choice = decide(
            lambda _kind: answers.pop(0),
            unrestricted=False,
            go_unrestricted=lambda: switched.append(True),
        )
        self.assertEqual(choice, RUN)
        self.assertEqual(switched, [True])


class FileRequestTests(unittest.TestCase):
    def setUp(self):
        paging.clear_all()

    def _call(
        self,
        confirm,
        timeout_ms,
        unrestricted=False,
        read="print(1)\n",
        on_queue=None,
        path="C:/work/a.py",
        opener=None,
        gate_box=None,
        log=None,
        tool="execute_python_file",
        arguments=None,
    ):
        http_server = _load_http_server()
        if log is not None:
            http_server.FreeCAD.Console = types.SimpleNamespace(
                PrintError=log.append
            )
        gate = JobGate()
        if gate_box is not None:
            gate_box.append(gate)

        class Executor:
            def call_on_main(self, fn):
                return fn()

            def file_turn(self):
                ctx = gate.file_turn()

                class _Turn:
                    def __enter__(self):
                        self.inner = ctx.__enter__()
                        return self

                    def __exit__(self, exc_type, exc, tb):
                        return ctx.__exit__(exc_type, exc, tb)

                    def queue(self, job):
                        self.inner.queue(job)
                        if on_queue is not None:
                            on_queue(job)

                return _Turn()

            def submit(
                self, code, output_queue, filepath=None, token=None
            ):
                if on_queue is not None:
                    on_queue((code, output_queue, filepath, token))

        handler = http_server.McpRequestHandler.__new__(
            http_server.McpRequestHandler
        )
        handler.server = types.SimpleNamespace(executor=Executor())
        sent = []
        handler._send_tool_result = lambda *args: sent.append(args[2])
        open_patch = (
            mock.patch("builtins.open", side_effect=opener)
            if opener is not None
            else mock.patch(
                "builtins.open", mock.mock_open(read_data=read)
            )
        )
        with (
            mock.patch.object(
                http_server.file_prompt, "confirm", side_effect=confirm
            ),
            mock.patch.object(
                http_server.config,
                "unrestricted",
                return_value=unrestricted,
            ),
            mock.patch.object(
                http_server.config,
                "max_response_timeout_ms",
                return_value=timeout_ms,
            ),
            mock.patch.object(
                http_server.config,
                "max_page_size_chars",
                return_value=8000,
            ),
            mock.patch.object(
                http_server.config,
                "max_history_retention_ms",
                return_value=60000,
            ),
            open_patch,
        ):
            handler._handle_tool_call(
                {
                    "id": 1,
                    "params": {
                        "name": tool,
                        "arguments": arguments
                        if arguments is not None
                        else {"filepath": path},
                    },
                },
                "2026-07-28",
            )
        return sent[0], gate

    def test_a_run_that_finishes_in_time_comes_back_in_the_answer(self):
        """Run, and a run that finishes in time, is the one answer."""
        seen = {}

        def confirm(path, code):
            seen["code"] = code
            return "run"

        def finish(job):
            seen["queued"] = job[0]
            job[1].put(("stdout", "ran\n"))
            paging.note_execution_finished(job[3], job[1], 60000)

        result, _gate = self._call(confirm, 2000, on_queue=finish)
        self.assertEqual(seen["code"], "print(1)\n")
        self.assertEqual(seen["queued"], "print(1)\n")
        self.assertEqual(
            result["page"], [{"stream": "stdout", "text": "ran\n"}]
        )
        self.assertFalse(result["has_more"])
        self.assertNotIn("error", result)
        self.assertNotIn(paging.SHOWN_NOTICE, result["page"][0]["text"])

    def test_the_timeout_includes_the_dialog_then_returns_the_notice(self):
        """An unanswered dialog returns the notice at the timeout."""
        release = threading.Event()

        def confirm(path, code):
            release.wait(2)
            return "decline"

        started = time.monotonic()
        try:
            result, _gate = self._call(confirm, 80)
            elapsed = time.monotonic() - started
            self.assertGreaterEqual(elapsed, 0.06)
            self.assertLess(elapsed, 0.4)
            self.assertTrue(result["has_more"])
            self.assertEqual(result["page_no"], 0)
            self.assertEqual(
                result["page"][0]["text"], paging.SHOWN_NOTICE
            )
            self.assertIn("job_token", result)
            self.assertNotIn("error", result)
            token = result["job_token"]
            paging._sweep_expired()
            waited = time.monotonic()
            again = paging.fetch(token, 1, 40, 8000)
            self.assertGreaterEqual(time.monotonic() - waited, 0.03)
            self.assertNotIn("page_no", again)
            self.assertTrue(again["has_more"])
            self.assertEqual(
                again["page"][0]["text"], paging.NOT_ANSWERED_YET
            )
            self.assertNotIn("error", again)
            release.set()
            declined = None
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                nxt = paging.fetch(token, 1, 30, 8000)
                if "error" in nxt:
                    declined = nxt
                    break
            self.assertIsNotNone(declined)
            self.assertIn("declined", declined["error"])
            self.assertFalse(declined["has_more"])
            self.assertEqual(declined["page_no"], 1)
        finally:
            release.set()

    def test_dont_run_within_the_timeout_is_an_error_in_that_answer(self):
        """Don't run within the timeout comes back in that answer."""
        def confirm(path, code):
            return "decline"

        result, gate = self._call(confirm, 2000)
        self.assertFalse(result["has_more"])
        self.assertEqual(result["page"], [])
        self.assertIn("declined", result["error"])
        self.assertIn("job_token", result)
        self.assertIsNone(gate.pop())

    def test_a_run_still_going_returns_the_usual_has_more_page(self):
        """Run, with the script still going, is an ordinary has_more page."""
        def confirm(path, code):
            return "run"

        def finish(job):
            job[1].put(("stdout", "partial\n"))

        result, _gate = self._call(confirm, 120, on_queue=finish)
        self.assertTrue(result["has_more"])
        self.assertEqual(result["page_no"], 0)
        self.assertEqual(
            result["page"], [{"stream": "stdout", "text": "partial\n"}]
        )

    def test_a_dialog_holds_the_queue_until_the_user_answers(self):
        """Jobs sent while the dialog is up run after that file."""
        box = []

        def confirm(path, code):
            self.assertIs(box[0].submit("during"), False)
            return "decline"

        result, held = self._call(confirm, 2000, gate_box=box)
        self.assertIn("declined", result["error"])
        self.assertEqual(held.pop(), "during")

    def test_unrestricted_mode_runs_a_file_without_the_dialog(self):
        """In Unrestricted mode a file runs without asking."""
        asked = []

        def confirm(path, code):
            asked.append(code)
            return "run"

        def finish(job):
            job[1].put(("stdout", "ran unasked\n"))
            paging.note_execution_finished(job[3], job[1], 60000)

        result, _gate = self._call(
            confirm, 2000, unrestricted=True, on_queue=finish
        )
        self.assertEqual(asked, [])
        self.assertEqual(
            result["page"],
            [{"stream": "stdout", "text": "ran unasked\n"}],
        )
        self.assertFalse(result["has_more"])

    def test_the_notice_is_true_before_the_dialog_is_shown(self):
        """The notice does not say a waiting file has been shown."""
        self.assertEqual(
            paging.SHOWN_NOTICE,
            "This file is waiting for the user's answer in FreeCAD.\n",
        )

    def test_a_dialog_that_fails_reports_its_own_error(self):
        """A dialog that fails is logged, and is not a user decline."""
        log = []

        def confirm(path, code):
            raise RuntimeError("boom")

        result, _gate = self._call(confirm, 2000, log=log)
        self.assertEqual(result["error"], "The file dialog failed: boom")
        self.assertFalse(result["has_more"])
        self.assertEqual(result["page"], [])
        self.assertEqual(
            log, ["[MCP Bridge] The file dialog failed: boom\n"]
        )

    def test_a_code_dialog_that_fails_says_the_code_dialog_failed(self):
        """For inline code the failure names the code dialog."""
        log = []

        def confirm(path, code):
            raise RuntimeError("boom")

        result, _gate = self._call(
            confirm,
            2000,
            log=log,
            tool="execute_python",
            arguments={"code": "print(2)\n"},
        )
        self.assertEqual(result["error"], "The code dialog failed: boom")
        self.assertFalse(result["has_more"])
        self.assertEqual(
            log, ["[MCP Bridge] The code dialog failed: boom\n"]
        )

    def test_two_file_dialogs_ask_one_at_a_time(self):
        """A second file asks only after the first dialog ends."""
        http_server = _load_http_server()
        gate = JobGate()
        order = []
        release = {
            "A": threading.Event(),
            "B": threading.Event(),
        }

        def confirm(path, code):
            order.append(code + "-start")
            release[code].wait(2)
            order.append(code + "-end")
            return "decline"

        class Executor:
            def call_on_main(self, fn):
                return fn()

            def file_turn(self):
                return gate.file_turn()

        handler = http_server.McpRequestHandler.__new__(
            http_server.McpRequestHandler
        )
        handler.server = types.SimpleNamespace(executor=Executor())
        first = paging.start_page()
        second = paging.start_page()
        with mock.patch.object(
            http_server.file_prompt, "confirm", side_effect=confirm
        ), mock.patch.object(
            http_server.config,
            "max_history_retention_ms",
            return_value=60000,
        ):
            one = threading.Thread(
                target=handler._ask_file,
                args=("a.py", "A", first[0], first[1], 60000),
            )
            two = threading.Thread(
                target=handler._ask_file,
                args=("b.py", "B", second[0], second[1], 60000),
            )
            one.start()
            time.sleep(0.02)
            two.start()
            time.sleep(0.05)
            self.assertEqual(order, ["A-start"])
            release["A"].set()
            deadline = time.monotonic() + 2
            while order != ["A-start", "A-end", "B-start"]:
                if time.monotonic() > deadline:
                    break
                time.sleep(0.01)
            self.assertEqual(
                order, ["A-start", "A-end", "B-start"]
            )
            release["B"].set()
            one.join(2)
            two.join(2)
        self.assertEqual(
            order, ["A-start", "A-end", "B-start", "B-end"]
        )

    def test_inline_code_uses_the_code_dialog_and_the_same_wait(self):
        """execute_python asks, with no path, on the same timeout."""
        seen = {}

        def confirm(path, code):
            seen["path"] = path
            seen["code"] = code
            return "decline"

        result, _gate = self._call(
            confirm,
            2000,
            tool="execute_python",
            arguments={"code": "print(2)\n"},
        )
        self.assertIsNone(seen["path"])
        self.assertEqual(seen["code"], "print(2)\n")
        self.assertEqual(result["error"], CODE_DECLINE)
        self.assertFalse(result["has_more"])
        self.assertEqual(
            CODE_INTRO,
            "An agent asks to run this code in FreeCAD with your "
            "full permissions:",
        )

    def test_a_repeat_of_the_notice_returns_at_once(self):
        """The stored notice page does not drain. The next page does."""
        token, _queue = paging.start_page()
        paging.mark_awaiting_user(token)
        notice = paging.remember_shown_notice(token, paging.CODE_NOTICE)
        started = time.monotonic()
        replay = paging.fetch(token, 0, 500, 8000)
        self.assertLess(time.monotonic() - started, 0.15)
        self.assertEqual(replay["page"], notice["page"])
        waited = time.monotonic()
        nxt = paging.fetch(token, 1, 200, 8000)
        elapsed = time.monotonic() - waited
        self.assertGreaterEqual(elapsed, 0.18)
        self.assertLess(elapsed, 0.5)
        self.assertEqual(
            nxt["page"][0]["text"], paging.NOT_ANSWERED_YET
        )
        self.assertNotIn("page_no", nxt)

class ModeAndQueueTests(unittest.TestCase):
    def test_the_confirm_says_what_unrestricted_means(self):
        """The second dialog names the mode and what it allows."""
        self.assertEqual(
            MODE_BODY,
            "In Unrestricted mode, any code an agent sends runs with "
            "your full permissions, unseen. Are you really sure?",
        )

    def test_the_two_mode_icons_are_drawn_as_one_set(self):
        """The shield and the triangle ship, on the same 24-unit grid
        with the same outline width."""
        ask = Path(mode_icon_path(False))
        unrestricted = Path(mode_icon_path(True))
        self.assertEqual(ask.name, "mode-ask.svg")
        self.assertEqual(unrestricted.name, "mode-unrestricted.svg")
        svg = "{http://www.w3.org/2000/svg}"
        outlines = []
        for icon in (ask, unrestricted):
            root = ET.parse(icon).getroot()
            self.assertEqual(root.tag, f"{svg}svg")
            self.assertEqual(root.get("viewBox"), "0 0 24 24")
            outlines.append(root.find(f"{svg}path").get("stroke-width"))
        self.assertEqual(outlines, ["1.5", "1.5"])

    def test_choosing_a_mode_asks_once_and_only_yes_goes_unrestricted(self):
        """Going Unrestricted asks once. Going back to Ask does not ask.
        Setting the box never runs its handler again."""

        class Box:
            def __init__(self):
                self.index = 1
                self.blocked = False
                self.reentered = 0

            def blockSignals(self, on):
                self.blocked = on

            def setCurrentIndex(self, value):
                self.index = value
                if not self.blocked:
                    self.reentered += 1
                    commit_mode(self, bool(value), ask)

        asks = []

        def ask():
            asks.append("ask")
            return "Yes"

        box = Box()
        self.assertIs(commit_mode(box, True, ask), True)
        self.assertEqual(asks, ["ask"])
        self.assertEqual(box.reentered, 0)
        self.assertEqual(box.index, 1)

        def refuse():
            asks.append("no")
            return "No"

        off = Box()
        self.assertIs(commit_mode(off, True, refuse), False)
        self.assertEqual(off.index, 0)
        self.assertEqual(off.reentered, 0)

        back = Box()
        self.assertIs(commit_mode(back, False, ask), False)
        self.assertEqual(back.index, 0)
        self.assertEqual(asks, ["ask", "no"])
        # The On/Off button's menu has no box.
        self.assertIs(commit_mode(None, True, ask), True)

    def test_a_file_runs_ahead_of_jobs_submitted_while_its_dialog_is_up(self):
        """Jobs sent during the dialog run after that file."""
        gate = JobGate()
        self.assertIs(gate.submit("earlier"), True)
        with gate.file_turn() as turn:
            self.assertIsNone(gate.pop())
            self.assertIs(gate.submit("during"), False)
            self.assertIsNone(gate.pop())
            turn.queue("file")
        self.assertEqual(gate.pop(), "earlier")
        self.assertEqual(gate.pop(), "file")
        self.assertEqual(gate.pop(), "during")
        self.assertIsNone(gate.pop())

    def test_a_declined_file_releases_jobs_that_waited(self):
        """Don't run does not queue the file, and waiting jobs then run."""
        gate = JobGate()
        with gate.file_turn() as _turn:
            gate.submit("during")
        self.assertEqual(gate.pop(), "during")

    def test_the_waiting_line_counts_jobs_not_yet_taken(self):
        """A running job sees later jobs as queued behind it."""
        gate = JobGate()
        line = JobLine(gate)
        self.assertTrue(line.empty())
        gate.submit("earlier")
        with gate.file_turn() as turn:
            self.assertIs(gate.submit("during"), False)
            self.assertEqual(line.qsize(), 2)
            turn.queue("file")
            self.assertEqual(line.qsize(), 2)
        self.assertEqual(line.qsize(), 3)
        self.assertFalse(line.empty())
        gate.pop()
        self.assertEqual(line.qsize(), 2)

    def test_a_second_file_turn_waits_until_the_first_releases(self):
        """Only one file dialog holds the queue at a time."""
        import threading
        import time

        gate = JobGate()
        order = []

        def hold(name):
            with gate.file_turn():
                order.append(name + "-start")
                time.sleep(0.05)
                order.append(name + "-end")

        first = threading.Thread(target=hold, args=("one",))
        second = threading.Thread(target=hold, args=("two",))
        first.start()
        time.sleep(0.01)
        second.start()
        first.join()
        second.join()
        self.assertEqual(
            order, ["one-start", "one-end", "two-start", "two-end"]
        )


if __name__ == "__main__":
    unittest.main()
