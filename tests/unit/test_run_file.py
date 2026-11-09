"""Requirements on asking before execute_python_file runs a file."""

import importlib
import sys
import types
import unittest
from unittest import mock

from freecad.mcp_bridge.file_prompt import DECLINE, RUN, decide, decline_message


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
    def test_the_dialog_finishes_before_the_timeout_and_runs_the_shown_text(self):
        """The response timeout starts after the dialog, on the text it showed."""
        freecad = types.SimpleNamespace()
        order = []
        shown = {}

        class Executor:
            def call_on_main(self, fn):
                order.append("dialog")
                return fn()

            def submit(self, code, _queue, filepath, token=None):
                order.append("submit")
                shown["code"] = code
                shown["path"] = filepath

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
                mock.patch.object(http_server.paging, "fetch", side_effect=fetch),
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
            handler.server = types.SimpleNamespace(
                executor=types.SimpleNamespace(
                    call_on_main=lambda fn: fn(),
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
                mock.patch("builtins.open", mock.mock_open(read_data="x")),
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


if __name__ == "__main__":
    unittest.main()
