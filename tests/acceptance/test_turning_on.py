"""Requirements on the address and port the bridge listens on when turned on.

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
from tests.acceptance.test_transport import non_loopback_address

PORT = urlsplit(mcp.BRIDGE_URL).port
NO_PREFERENCE = -1  # the Port preference is unset: the default applies

PREFERENCES = """
from PySide.QtCore import QTimer
from freecad.mcp_bridge import bridge
from freecad.mcp_bridge.constants import PREF_GROUP

params = App.ParamGet(PREF_GROUP)
action = Gui.Command.get(App.MCPBridgeCommand).getAction()[0]


def set_port(port):
    if port == -1:
        params.RemInt('Port')
        params.RemString('ListenAddress')
    else:
        params.SetString('ListenAddress', f'127.0.0.1:{port}')
"""


def saved_port():
    """The port in ListenAddress or Port, or NO_PREFERENCE if unset."""
    code = PREFERENCES + """
raw = params.GetString('ListenAddress', '').strip()
if raw:
    print(int(raw.split()[0].rsplit(':', 1)[1]))
else:
    print(params.GetInt('Port', -1))
"""
    return int(mcp.run(code))


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
from PySide.QtWidgets import (
    QApplication,
    QDockWidget,
    QPlainTextEdit,
    QTextEdit,
)

seen = {{}}
dock = Gui.getMainWindow().findChild(QDockWidget, 'Report view')
was_visible = dock is not None and dock.isVisible()


def report_view_text():
    if dock is None:
        return ''
    dock.show()
    QApplication.processEvents()
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


def migrate_and_save_preferences(legacy_port, seen_file):
    """Code that sets a legacy `Port` with `ListenAddress` unset, opens the
    MCP Bridge Preferences page, records what `listenAddressTextEdit` shows,
    clicks OK, and restarts the bridge on the saved address."""
    return PREFERENCES + f"""
import json
from PySide.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QTextEdit,
)

seen = {{}}
mw = Gui.getMainWindow()


def inspect_and_click_ok():
    dlg = next(
        (
            d
            for d in mw.findChildren(QDialog)
            if d.findChild(QTextEdit, 'listenAddressTextEdit') is not None
        ),
        None,
    )
    if dlg is None:
        QTimer.singleShot(100, inspect_and_click_ok)
        return
    seen['dialog_found'] = True
    box = dlg.findChild(QTextEdit, 'listenAddressTextEdit')
    seen['box_text'] = box.toPlainText().strip() if box is not None else None
    btn_box = dlg.findChild(QDialogButtonBox)
    ok_btn = (
        btn_box.button(QDialogButtonBox.Ok) if btn_box is not None else None
    )
    if ok_btn is not None:
        ok_btn.click()
    else:
        dlg.accept()


def step_open_prefs():
    if bridge.is_running():
        action.trigger()
    params.RemInt('Port')
    params.RemString('ListenAddress')
    params.SetInt('Port', {legacy_port})
    QTimer.singleShot(250, inspect_and_click_ok)
    Gui.showPreferences('MCP Bridge', 0)
    seen['saved_listen_address'] = params.GetString(
        'ListenAddress', ''
    ).strip()
    with open({seen_file!r}, 'w', encoding='utf-8') as handle:
        json.dump(seen, handle)
    action.trigger()


QTimer.singleShot(300, step_open_prefs)
"""


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

    def test_an_upgraded_port_survives_opening_and_saving_preferences(self):
        """An upgraded Port preference is shown in Preferences and kept on
        OK."""
        before = saved_port()
        legacy_port = free_port()
        with tempfile.TemporaryDirectory() as folder:
            seen_file = os.path.join(folder, "seen.json")
            mcp.run(migrate_and_save_preferences(legacy_port, seen_file))
            recorded = wait_for_file(seen_file)
            with bridge_on(legacy_port):
                reached = mcp.wait_until_reachable(30)
                if reached:
                    output = mcp.run("print('on migrated port')")
                    mcp.run(restart_on(before))
            if not reached and answers(PORT):
                mcp.run(restart_on(before))
            self.assertTrue(
                mcp.wait_until_reachable(30), "the bridge did not come back"
            )
            self.assertTrue(
                recorded, "the preferences dialog check was not recorded"
            )
            with open(seen_file, encoding="utf-8") as handle:
                seen = json.load(handle)
        expected_entry = f"127.0.0.1:{legacy_port}"
        self.assertTrue(seen.get("dialog_found"))
        self.assertEqual(seen.get("box_text"), expected_entry)
        self.assertEqual(seen.get("saved_listen_address"), expected_entry)
        self.assertTrue(reached, f"nothing answered on port {legacy_port}")
        self.assertEqual(output, "on migrated port\n")

    def test_an_occupied_port_is_reported_and_no_other_port_is_used(self):
        """An occupied port is reported at startup; no other port is used."""
        before = saved_port()
        with tempfile.TemporaryDirectory() as folder:
            seen_file = os.path.join(folder, "seen.json")
            with socket.socket() as occupier:
                occupier.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
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
        self.assertIn("Change the listen address in Edit", seen["report"])
        self.assertIn("Preferences", seen["report"])


def non_loopback_flow(address, seen_file, continue_file):
    """Code that exercises declining, accepting, and restarting at `address`,
    waiting for `continue_file` while the bridge is up on `address` so the
    test runner can talk to both `address` and `127.0.0.1`, then restores
    preferences and restarts on loopback."""
    entry = f"{address}:{PORT}"
    loopback_entry = f"127.0.0.1:{PORT}"
    return PREFERENCES + f"""
import json
import os
import socket
from PySide.QtWidgets import (
    QApplication,
    QDockWidget,
    QMessageBox,
    QPlainTextEdit,
    QTextEdit,
)

seen = {{}}
mw = Gui.getMainWindow()
dock = mw.findChild(QDockWidget, 'Report view')
was_visible = dock is not None and dock.isVisible()
orig_addr = params.GetString('ListenAddress', '')
orig_accepted = params.GetString('AcceptedListenAddresses', '')


def report_view_text():
    if dock is None:
        return ''
    dock.show()
    QApplication.processEvents()
    for kind in (QTextEdit, QPlainTextEdit):
        for view in dock.findChildren(kind):
            return view.toPlainText()
    return ''


def can_connect(host, port):
    try:
        socket.create_connection((host, port), timeout=1).close()
        return True
    except OSError:
        return False


def click_dialog_button(label, prefix):
    dlg = mw.findChild(QMessageBox, 'MCPBridgeRiskDialog')
    if dlg is None:
        widget = QApplication.activeModalWidget()
        if isinstance(widget, QMessageBox):
            dlg = widget
    if dlg is None:
        seen[prefix + '_dialog_found'] = False
        return
    seen[prefix + '_dialog_found'] = True
    seen[prefix + '_title'] = dlg.windowTitle()
    seen[prefix + '_text'] = dlg.text()
    seen[prefix + '_port_open_during_dialog'] = can_connect(
        {address!r}, {PORT}
    )
    for btn in dlg.buttons():
        if btn.text() == label:
            btn.click()
            return


def step_decline():
    if bridge.is_running():
        action.trigger()
    # First check that an entry without a port is rejected and leaves the
    # bridge off.
    params.SetString('ListenAddress', {address!r})
    before_no_port = len(report_view_text())
    action.trigger()
    seen['no_port_running'] = bridge.is_running()
    seen['no_port_status'] = mw.statusBar().currentMessage()
    seen['no_port_report'] = report_view_text()[before_no_port:]
    params.SetString(
        'ListenAddress', {loopback_entry!r} + '\\n' + {entry!r}
    )
    params.RemString('AcceptedListenAddresses')
    QTimer.singleShot(
        150, lambda: click_dialog_button('Cancel', 'decline')
    )
    action.trigger()
    seen['declined_running'] = bridge.is_running()
    seen['declined_port_open'] = can_connect({address!r}, {PORT})
    seen['declined_status'] = mw.statusBar().currentMessage()
    seen['declined_accepted'] = params.GetString(
        'AcceptedListenAddresses', ''
    )
    QTimer.singleShot(200, step_accept)


def step_accept():
    before = len(report_view_text())
    QTimer.singleShot(
        150,
        lambda: click_dialog_button('Accept Risks and Start', 'accept'),
    )
    action.trigger()
    seen['accepted_running'] = bridge.is_running()
    seen['accepted_status'] = mw.statusBar().currentMessage()
    seen['accepted_report'] = report_view_text()[before:]
    seen['accepted_saved'] = params.GetString('AcceptedListenAddresses', '')
    with open({seen_file!r} + '.ready', 'w', encoding='utf-8') as handle:
        json.dump(seen, handle)
    QTimer.singleShot(200, wait_for_runner)


def wait_for_runner():
    if not os.path.exists({continue_file!r}):
        QTimer.singleShot(200, wait_for_runner)
        return
    # Restart with the already-accepted list: no dialog should open,
    # and the warning must appear again.
    if bridge.is_running():
        action.trigger()
    before = len(report_view_text())
    QTimer.singleShot(
        150, lambda: click_dialog_button('Cancel', 'second_start')
    )
    action.trigger()
    seen['second_running'] = bridge.is_running()
    seen['second_status'] = mw.statusBar().currentMessage()
    seen['second_report'] = report_view_text()[before:]
    # Start with the LAN entry alone (removing 127.0.0.1): loopback
    # must not be bound, while the LAN entry is bound.
    if bridge.is_running():
        action.trigger()
    params.SetString('ListenAddress', {entry!r})
    action.trigger()
    seen['lan_only_running'] = bridge.is_running()
    seen['lan_only_lan_open'] = can_connect({address!r}, {PORT})
    seen['lan_only_loopback_open'] = can_connect('127.0.0.1', {PORT})
    if bridge.is_running():
        action.trigger()
    # Restore original preferences and restart on loopback.
    if orig_addr:
        params.SetString('ListenAddress', orig_addr)
    else:
        params.RemString('ListenAddress')
    if orig_accepted:
        params.SetString('AcceptedListenAddresses', orig_accepted)
    else:
        params.RemString('AcceptedListenAddresses')
    if dock is not None and not was_visible:
        dock.hide()
    action.trigger()
    with open({seen_file!r}, 'w', encoding='utf-8') as handle:
        json.dump(seen, handle)


QTimer.singleShot(300, step_decline)
"""


class ListenAddressTests(unittest.TestCase):
    def test_non_loopback_address_requires_acceptance_binds_list_and_warns(
        self,
    ):
        """Non-loopback entries ask once, bind the list, and warn."""
        address = non_loopback_address()
        if address is None:
            self.skipTest("this machine has no non-loopback IPv4 address")
        with tempfile.TemporaryDirectory() as folder:
            seen_file = os.path.join(folder, "seen.json")
            ready_file = seen_file + ".ready"
            continue_file = os.path.join(folder, "continue")
            mcp.run(non_loopback_flow(address, seen_file, continue_file))
            self.assertTrue(
                wait_for_file(ready_file),
                "the bridge did not reach the accepted state",
            )
            with mock.patch.object(
                mcp, "BRIDGE_URL", f"http://{address}:{PORT}/mcp"
            ):
                self.assertTrue(
                    mcp.wait_until_reachable(15),
                    f"nothing answered on {address}:{PORT}",
                )
                lan_out = mcp.run("print('from lan address')")
            loopback_out = mcp.run("print('from loopback alongside')")
            open(continue_file, "w", encoding="utf-8").close()
            self.assertTrue(
                wait_for_file(seen_file),
                "the second-start and restore step did not finish",
            )
            self.assertTrue(
                mcp.wait_until_reachable(30), "the bridge did not come back"
            )
            with open(seen_file, encoding="utf-8") as handle:
                seen = json.load(handle)

        self.assertEqual(lan_out, "from lan address\n")
        self.assertEqual(loopback_out, "from loopback alongside\n")
        self.assertFalse(seen.get("no_port_running"))
        self.assertIn(address, seen.get("no_port_status", ""))
        self.assertIn(
            "Change the listen address in Edit",
            seen.get("no_port_report", ""),
        )
        self.assertTrue(seen.get("decline_dialog_found"))
        self.assertFalse(seen.get("decline_port_open_during_dialog"))
        self.assertEqual(
            seen.get("decline_title"),
            "MCP Bridge — Listen beyond this machine?",
        )
        self.assertIn(f"{address}:{PORT}", seen.get("decline_text", ""))
        self.assertIn("No authentication", seen.get("decline_text", ""))
        self.assertIn("No encryption", seen.get("decline_text", ""))
        self.assertIn("Code execution", seen.get("decline_text", ""))
        self.assertFalse(seen.get("declined_running"))
        self.assertFalse(seen.get("declined_port_open"))
        self.assertEqual(seen.get("declined_status"), "MCP Bridge: Offline")
        self.assertEqual(seen.get("declined_accepted"), "")

        self.assertTrue(seen.get("accept_dialog_found"))
        self.assertFalse(seen.get("accept_port_open_during_dialog"))
        self.assertTrue(seen.get("accepted_running"))
        self.assertEqual(
            json.loads(seen.get("accepted_saved", "[]")),
            [f"{address}:{PORT}"],
        )
        expected_status = (
            f"MCP Bridge: Listening on 127.0.0.1:{PORT}, "
            f"{address}:{PORT} (warning: no authentication or encryption)"
        )
        expected_warning = (
            f"[MCP Bridge] WARNING: listening on 127.0.0.1:{PORT}, "
            f"{address}:{PORT} with no authentication or encryption. Any "
            "machine that can reach these addresses can run Python code in "
            f"FreeCAD as your user account: {address}:{PORT}."
        )
        self.assertEqual(seen.get("accepted_status"), expected_status)
        self.assertIn(expected_warning, seen.get("accepted_report", ""))

        self.assertFalse(seen.get("second_start_dialog_found"))
        self.assertTrue(seen.get("second_running"))
        self.assertEqual(seen.get("second_status"), expected_status)
        self.assertIn(expected_warning, seen.get("second_report", ""))

        self.assertTrue(seen.get("lan_only_running"))
        self.assertTrue(seen.get("lan_only_lan_open"))
        self.assertFalse(seen.get("lan_only_loopback_open"))


if __name__ == "__main__":
    unittest.main()
