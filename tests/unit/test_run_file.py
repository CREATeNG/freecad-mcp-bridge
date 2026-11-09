"""Requirements on asking before execute_python_file runs a file."""

import importlib
import sys
import types
import unittest
from unittest import mock

from freecad.mcp_bridge.file_prompt import (
    DECLINE,
    RUN,
    decide,
    decline_message,
    trust_checkbox_sticks,
    trust_label_visible,
)
from freecad.mcp_bridge.job_gate import JobGate, JobLine


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
    def test_the_timeout_starts_after_the_dialog(self):
        """The response timeout starts after the dialog returns."""
        freecad = types.SimpleNamespace()
        order = []
        shown = {}

        class Executor:
            def call_on_main(self, fn):
                order.append("dialog")
                return fn()

            def file_turn(self):
                outer = self

                class _Turn:
                    def queue(self, job):
                        order.append("submit")
                        shown["code"] = job[0]
                        shown["path"] = job[2]

                    def __enter__(self):
                        return self

                    def __exit__(self, exc_type, exc, tb):
                        return False

                return _Turn()

        def fetch(token, page_no, timeout_ms, page_size):
            order.append("fetch")
            shown["timeout"] = timeout_ms
            return {"has_more": False, "page": [], "page_no": 0}

        def confirm(path, code):
            shown["asked"] = code
            return "run"

        fake_executor = types.ModuleType("freecad.mcp_bridge.executor")
        fake_executor.Executor = object
        stubs = {
            "FreeCAD": freecad,
            "freecad.mcp_bridge.executor": fake_executor,
        }
        with mock.patch.dict(sys.modules, stubs):
            sys.modules.pop("freecad.mcp_bridge.config", None)
            sys.modules.pop("freecad.mcp_bridge.http_server", None)
            http_server = importlib.import_module(
                "freecad.mcp_bridge.http_server"
            )
            handler = http_server.McpRequestHandler.__new__(
                http_server.McpRequestHandler
            )
            handler.server = types.SimpleNamespace(executor=Executor())
            sent = []
            handler._send_tool_result = lambda *args: sent.append(args)
            code = "print(1)\n"
            with (
                mock.patch.object(
                    http_server.file_prompt, "confirm", side_effect=confirm
                ),
                mock.patch.object(
                    http_server.paging, "start_page", return_value=("tok", "q")
                ),
                mock.patch.object(
                    http_server.paging, "fetch", side_effect=fetch
                ),
                mock.patch.object(
                    http_server, "client_is_open", return_value=True
                ),
                mock.patch.object(
                    http_server.config,
                    "max_response_timeout_ms",
                    return_value=15000,
                ),
                mock.patch.object(
                    http_server.config,
                    "max_page_size_chars",
                    return_value=100,
                ),
                mock.patch("builtins.open", mock.mock_open(read_data=code)),
            ):
                handler._handle_tool_call(
                    {
                        "id": 1,
                        "params": {
                            "name": "execute_python_file",
                            "arguments": {"filepath": "C:/work/a.py"},
                        },
                    },
                    "2026-07-28",
                )
        self.assertEqual(order, ["dialog", "submit", "fetch"])
        self.assertEqual(shown["asked"], code)
        self.assertEqual(shown["code"], code)
        self.assertEqual(shown["timeout"], 15000)
        self.assertEqual(sent[0][2]["page"], [])
        self.assertEqual(sent[0][2]["page_no"], 0)

    def test_dont_run_returns_a_decline_and_starts_no_job(self):
        """Don't run is a tool error and does not queue a job."""
        freecad = types.SimpleNamespace()
        fake_executor = types.ModuleType("freecad.mcp_bridge.executor")
        fake_executor.Executor = object
        stubs = {
            "FreeCAD": freecad,
            "freecad.mcp_bridge.executor": fake_executor,
        }
        with mock.patch.dict(sys.modules, stubs):
            sys.modules.pop("freecad.mcp_bridge.config", None)
            sys.modules.pop("freecad.mcp_bridge.http_server", None)
            http_server = importlib.import_module(
                "freecad.mcp_bridge.http_server"
            )
            handler = http_server.McpRequestHandler.__new__(
                http_server.McpRequestHandler
            )
            submitted = []
            class _Turn:
                def queue(self, job):
                    submitted.append(True)

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return False

            handler.server = types.SimpleNamespace(
                executor=types.SimpleNamespace(
                    call_on_main=lambda fn: fn(),
                    file_turn=lambda: _Turn(),
                    submit=lambda *a, **k: submitted.append(True),
                )
            )
            sent = []
            handler._send_tool_result = lambda *args: sent.append(args[2])
            with (
                mock.patch.object(
                    http_server.file_prompt,
                    "confirm",
                    return_value="decline",
                ),
                mock.patch(
                    "builtins.open", mock.mock_open(read_data="x")
                ),
                mock.patch.object(
                    http_server, "client_is_open", return_value=True
                ),
                mock.patch.object(
                    http_server.config,
                    "max_response_timeout_ms",
                    return_value=15000,
                ),
                mock.patch.object(
                    http_server.config,
                    "max_page_size_chars",
                    return_value=100,
                ),
            ):
                handler._handle_tool_call(
                    {
                        "id": 2,
                        "params": {
                            "name": "execute_python_file",
                            "arguments": {"filepath": "C:/work/a.py"},
                        },
                    },
                    "2026-07-28",
                )
        self.assertEqual(submitted, [])
        self.assertFalse(sent[0]["has_more"])
        self.assertEqual(sent[0]["page"], [])
        self.assertIn("declined", sent[0]["error"])
        self.assertNotIn("job_token", sent[0])


class TrustAndQueueTests(unittest.TestCase):
    def test_ticking_trust_sticks_only_after_yes(self):
        """Ticking Trust any file stays on only when the user says Yes."""
        self.assertIs(trust_checkbox_sticks(False, "Yes"), False)
        self.assertIs(trust_checkbox_sticks(True, "No"), False)
        self.assertIs(trust_checkbox_sticks(True, "Yes"), True)

    def test_the_trust_label_shows_whether_or_not_the_button_is_shown(self):
        """The marker is on whenever trust is on, and ignores the button."""
        self.assertIs(trust_label_visible(True, True), True)
        self.assertIs(trust_label_visible(True, False), True)
        self.assertIs(trust_label_visible(False, True), False)
        self.assertIs(trust_label_visible(False, False), False)

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

    def test_a_closed_client_socket_is_not_open(self):
        """A departed client is detected on the request socket."""
        import socket

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
            http_server = importlib.import_module(
                "freecad.mcp_bridge.http_server"
            )
        client_is_open = http_server.client_is_open

        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        client = socket.create_connection(server.getsockname())
        accepted, _addr = server.accept()
        try:
            self.assertIs(client_is_open(accepted), True)
            client.close()
            self.assertIs(client_is_open(accepted), False)
        finally:
            accepted.close()
            server.close()


if __name__ == "__main__":
    unittest.main()
