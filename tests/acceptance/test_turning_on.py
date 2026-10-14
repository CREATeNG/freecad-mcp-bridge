"""Requirements on the port the bridge listens on when it is turned on.

Each test name, and the first line of its docstring, states one requirement
as a user and an agent experience it, against a real bridge in a real
FreeCAD. The user's preference change and toolbar clicks are made through
the bridge itself (execute_python), on timers that fire after the job has
returned, since turning the bridge off closes the port the job came in on.
Each test puts the port preference back and the bridge back on that port
before it ends. This module sorts last, so a failure to do so cannot fail
the other modules' tests.
"""

import json
import os
import socket
import tempfile
import time
import unittest
from contextlib import contextmanager
from unittest import mock
from urllib.parse import urlsplit

from tests.acceptance import mcp_client as mcp

PORT = urlsplit(mcp.BRIDGE_URL).port
NO_PREFERENCE = -1  # the Port preference is unset: the default applies

PREFERENCES = """
from PySide.QtCore import QTimer
from freecad.mcp_bridge import bridge
from freecad.mcp_bridge.constants import PREF_GROUP

params = App.ParamGet(PREF_GROUP)
action = App.MCPBridgeAction  # the toolbar's On/Off action


def set_port(port):
    if port == -1:
        params.RemInt('Port')
    else:
        params.SetInt('Port', port)
"""


def saved_port():
    """The Port preference as saved, or NO_PREFERENCE if unset."""
    return int(mcp.run(PREFERENCES + "print(params.GetInt('Port', -1))"))


def restart_on(port):
    """Code that saves `port` as the Port preference, then, once the job
    has returned, clicks the toolbar toggle off and on again."""
    return PREFERENCES + (
        f"set_port({port})\n"
        "QTimer.singleShot(500, action.trigger)\n"
        "QTimer.singleShot(\n"
        "    1500, lambda: None if bridge.is_running() else action.trigger()\n"
        ")\n"
    )


def start_on_occupied(port, restore_to, seen_file):
    """Code that, once the job has returned, turns the bridge off, saves
    `port` and turns it on, records what the user is shown, then puts the
    Port preference back to `restore_to`, the Report view back as it was,
    and turns the bridge on again."""
    return PREFERENCES + f"""
import json
from PySide.QtWidgets import QDockWidget, QPlainTextEdit, QTextEdit

seen = {{}}
dock = Gui.getMainWindow().findChild(QDockWidget, 'Report view')
was_visible = dock is not None and dock.isVisible()


def report_view_text():
    if dock is None:
        return ''
    dock.show()
    for kind in (QTextEdit, QPlainTextEdit):
        for view in dock.findChildren(kind):
            return view.toPlainText()
    return ''


def turn_on_at_the_occupied_port():
    set_port({port})
    seen['report_before'] = len(report_view_text())
    action.trigger()
    seen['running'] = bridge.is_running()
    seen['status'] = Gui.getMainWindow().statusBar().currentMessage()


def record_and_restore():
    seen['report'] = report_view_text()[seen.pop('report_before'):]
    if dock is not None and not was_visible:
        dock.hide()
    set_port({restore_to})
    if bridge.is_running():
        action.trigger()
    with open({seen_file!r}, 'w', encoding='utf-8') as handle:
        json.dump(seen, handle)
    action.trigger()


QTimer.singleShot(500, action.trigger)
QTimer.singleShot(1500, turn_on_at_the_occupied_port)
QTimer.singleShot(3000, record_and_restore)
"""


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def answers(port):
    """True if anything accepts a connection on 127.0.0.1:`port`."""
    try:
        socket.create_connection(("127.0.0.1", port), timeout=5).close()
    except OSError:
        return False
    return True


@contextmanager
def bridge_on(port):
    """Talk to the bridge on `port` for the duration."""
    with mock.patch.object(mcp, "BRIDGE_URL", f"http://127.0.0.1:{port}/mcp"):
        yield


def wait_for_file(path, timeout_s=30):
    deadline = time.monotonic() + timeout_s
    while not os.path.exists(path) and time.monotonic() < deadline:
        time.sleep(0.25)
    return os.path.exists(path)


class PortTests(unittest.TestCase):
    def test_a_changed_port_is_used_when_the_bridge_next_starts(self):
        """A changed port is used when the bridge is next turned on."""
        before = saved_port()
        new_port = free_port()
        mcp.run(restart_on(new_port))
        with bridge_on(new_port):
            reached = mcp.wait_until_reachable(30)
            if reached:
                old_port_answers = answers(PORT)
                output = mcp.run("print('on the new port')")
                mcp.run(restart_on(before))
        if not reached and answers(PORT):
            mcp.run(restart_on(before))  # it never left the old port
        self.assertTrue(
            mcp.wait_until_reachable(30), "the bridge did not come back"
        )
        self.assertTrue(reached, f"nothing answered on port {new_port}")
        self.assertFalse(old_port_answers, f"port {PORT} still answered")
        self.assertEqual(output, "on the new port\n")

    def test_an_occupied_port_is_reported_and_no_other_port_is_used(self):
        """An occupied port is reported at startup; no other port is used."""
        before = saved_port()
        with tempfile.TemporaryDirectory() as folder:
            seen_file = os.path.join(folder, "seen.json")
            with socket.socket() as occupier:
                occupier.bind(("127.0.0.1", 0))
                occupier.listen()
                port = occupier.getsockname()[1]
                mcp.run(start_on_occupied(port, before, seen_file))
                recorded = wait_for_file(seen_file)
            self.assertTrue(
                mcp.wait_until_reachable(30), "the bridge did not come back"
            )
            self.assertTrue(recorded, "the startup attempt was not recorded")
            with open(seen_file, encoding="utf-8") as handle:
                seen = json.load(handle)
        self.assertFalse(seen["running"], "the bridge started anyway")
        self.assertTrue(
            seen["status"].startswith("MCP Bridge: Could not start"),
            seen["status"],
        )
        self.assertIn(f"Could not bind 127.0.0.1:{port}", seen["report"])
        self.assertIn("Change the port in Edit", seen["report"])
        self.assertIn("Preferences", seen["report"])


if __name__ == "__main__":
    unittest.main()
