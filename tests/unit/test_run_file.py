"""Requirements on asking before execute_python_file runs a file."""

import importlib
import sys
import threading
import time
import types
import unittest
from unittest import mock

from freecad.mcp_bridge import paging
from freecad.mcp_bridge.file_prompt import (
    DECLINE,
    RUN,
    decide,
    decline_message,
    trust_checkbox_sticks,
    trust_label_visible,
)
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
    def test_trust_already_on_skips_the_dialog(self):
        """While Trust any file is on, the file runs without asking."""
        asked = []
        self.assertEqual(
            decide(asked.append, trusting=True, set_trust=lambda _v: None),
            RUN,
        )
        self.assertEqual(asked, [])

    def test_dont_run_is_a_decline_and_does_not_turn_trust_on(self):
        """Don't run tells the caller to decline and leaves trust off."""
        turned = []
        choice = decide(
            lambda _kind: "Don't run",
            trusting=False,
            set_trust=turned.append,
        )
        self.assertEqual(choice, DECLINE)
        self.assertEqual(turned, [])
        self.assertIn("declined", decline_message("/tmp/a.py"))

    def test_run_returns_run_without_turning_trust_on(self):
        """Run runs the file and does not turn trust on."""
        turned = []
        choice = decide(
            lambda _kind: "Run",
            trusting=False,
            set_trust=turned.append,
        )
        self.assertEqual(choice, RUN)
        self.assertEqual(turned, [])

    def test_no_on_the_second_dialog_returns_to_the_first(self):
        """No on Trust any file asks again; only Yes turns it on."""
        answers = ["Trust any file", "No", "Run"]
        turned = []
        choice = decide(
            lambda _kind: answers.pop(0),
            trusting=False,
            set_trust=turned.append,
        )
        self.assertEqual(choice, RUN)
        self.assertEqual(turned, [])

    def test_yes_on_the_second_dialog_turns_trust_on_and_runs(self):
        """Yes turns trust on, and the file that was shown then runs."""
        answers = ["Trust any file", "Yes"]
        turned = []
        choice = decide(
            lambda _kind: answers.pop(0),
            trusting=False,
            set_trust=turned.append,
        )
        self.assertEqual(choice, RUN)
        self.assertEqual(turned, [True])


class FileRequestTests(unittest.TestCase):
    def setUp(self):
        paging.clear_all()

    def _call(
        self,
        confirm,
        timeout_ms,
        trust=False,
        read="print(1)\n",
        on_queue=None,
        path="C:/work/a.py",
        opener=None,
        gate_box=None,
    ):
        http_server = _load_http_server()
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
                http_server.config, "trust_any_file", return_value=trust
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
                        "name": "execute_python_file",
                        "arguments": {"filepath": path},
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

    def test_a_trusted_file_runs_without_the_dialog(self):
        """Trust any file runs the file without asking."""
        asked = []

        def confirm(path, code):
            asked.append(code)
            return "run"

        def finish(job):
            job[1].put(("stdout", "trusted\n"))
            paging.note_execution_finished(job[3], job[1], 60000)

        result, _gate = self._call(
            confirm, 2000, trust=True, on_queue=finish
        )
        self.assertEqual(asked, [])
        self.assertEqual(
            result["page"], [{"stream": "stdout", "text": "trusted\n"}]
        )
        self.assertFalse(result["has_more"])

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

class TrustAndQueueTests(unittest.TestCase):
    def test_ticking_trust_sticks_only_after_yes(self):
        """Ticking Trust any file stays on only when the user says Yes."""
        self.assertIs(trust_checkbox_sticks(False, "Yes"), False)
        self.assertIs(trust_checkbox_sticks(True, "No"), False)
        self.assertIs(trust_checkbox_sticks(True, "Yes"), True)

    def test_the_trust_label_shows_only_while_trust_is_on(self):
        """The marker is on whenever trust is on."""
        self.assertIs(trust_label_visible(True), True)
        self.assertIs(trust_label_visible(False), False)

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
