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
import shutil
import socket
import ssl
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request
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
        """An upgraded Port is shown in Preferences and kept on OK."""
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
        self.assertIn("Change the listen addresses in Edit", seen["report"])
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
orig_req_token = params.GetBool('RequireToken', True)
orig_req_tls = params.GetBool('RequireTls', True)
orig_req_on_loopback = params.GetBool('RequireOnLoopback', False)
orig_token_hash = params.GetString('TokenHash', '')
orig_cert = params.GetString('TlsCertFile', '')
orig_key = params.GetString('TlsKeyFile', '')


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


def click_dialog_button(label, prefix, attempts=40):
    dlg = None
    for name in (
        'MCPBridgeFirstStartDialog',
        'MCPBridgeRiskDialog',
        'MCPBridgeTokenDialog',
    ):
        found = mw.findChild(QMessageBox, name)
        if found is not None and found.isVisible():
            dlg = found
            break
    if dlg is None:
        widget = QApplication.activeModalWidget()
        if isinstance(widget, QMessageBox):
            dlg = widget
    if dlg is None:
        if prefix in ('decline', 'accept') and attempts > 0:
            QTimer.singleShot(
                50,
                lambda: click_dialog_button(label, prefix, attempts - 1),
            )
            return
        seen[prefix + '_dialog_found'] = False
        return
    seen[prefix + '_dialog_found'] = True
    seen[prefix + '_title'] = dlg.windowTitle()
    seen[prefix + '_text'] = dlg.text()
    seen[prefix + '_port_open_during_dialog'] = can_connect(
        {address!r}, {PORT}
    )
    wanted = label
    if dlg.objectName() == 'MCPBridgeTokenDialog':
        wanted = 'OK'
    elif (
        dlg.objectName() == 'MCPBridgeFirstStartDialog'
        and label.startswith('Accept')
    ):
        wanted = 'Accept and Start'
    for btn in dlg.buttons():
        if btn.text() == wanted:
            if wanted not in ('OK', 'Cancel'):
                QTimer.singleShot(
                    50,
                    lambda: click_dialog_button(
                        'OK', prefix + '_token', 20
                    ),
                )
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
    params.SetBool('RequireToken', False)
    params.SetBool('RequireTls', False)
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
    params.SetBool('RequireToken', orig_req_token)
    params.SetBool('RequireTls', orig_req_tls)
    params.SetBool('RequireOnLoopback', orig_req_on_loopback)
    if orig_token_hash:
        params.SetString('TokenHash', orig_token_hash)
    else:
        params.RemString('TokenHash')
    if orig_cert:
        params.SetString('TlsCertFile', orig_cert)
    else:
        params.RemString('TlsCertFile')
    if orig_key:
        params.SetString('TlsKeyFile', orig_key)
    else:
        params.RemString('TlsKeyFile')
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
            "Change the listen addresses in Edit",
            seen.get("no_port_report", ""),
        )
        self.assertTrue(seen.get("decline_dialog_found"))
        self.assertFalse(seen.get("decline_port_open_during_dialog"))
        self.assertEqual(
            seen.get("decline_title"),
            "MCP Bridge: Listen beyond this machine?",
        )
        self.assertIn(f"{address}:{PORT}", seen.get("decline_text", ""))
        self.assertNotIn("No authentication", seen.get("decline_text", ""))
        self.assertIn("No encryption", seen.get("decline_text", ""))
        self.assertIn("bearer token", seen.get("decline_text", ""))
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
            f"{address}:{PORT} (warning: connected agents can run "
            "code with your full permissions; beyond loopback: "
            f"{address}:{PORT})"
        )
        expected_warning = (
            f"[MCP Bridge] WARNING: listening on 127.0.0.1:{PORT}, "
            f"{address}:{PORT} with no encryption. Any client with the "
            "bearer token that can reach these addresses can run "
            "Python code in FreeCAD as your user account: "
            f"{address}:{PORT}."
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

    def test_non_loopback_bearer_token_and_tls(self):
        """Non-loopback entries enforce bearer token and TLS options."""
        address = non_loopback_address()
        if address is None:
            self.skipTest("this machine has no non-loopback IPv4 address")
        openssl_bin = shutil.which("openssl") or (
            r"C:\Program Files\Git\usr\bin\openssl.exe"
            if os.path.exists(r"C:\Program Files\Git\usr\bin\openssl.exe")
            else None
        )
        if openssl_bin is None:
            self.skipTest("openssl is not available to make a throwaway cert")

        with tempfile.TemporaryDirectory() as folder:
            cert_path = os.path.join(folder, "cert.pem")
            key_path = os.path.join(folder, "key.pem")
            enc_cert_path = os.path.join(folder, "enc_cert.pem")
            enc_key_path = os.path.join(folder, "enc_key.pem")
            cnf_path = os.path.join(folder, "openssl.cnf")
            with open(cnf_path, "w", encoding="utf-8") as handle:
                handle.write(
                    "[req]\n"
                    "distinguished_name = dn\n"
                    "x509_extensions = v3\n"
                    "prompt = no\n"
                    "[dn]\n"
                    f"CN = {address}\n"
                    "[v3]\n"
                    f"subjectAltName = IP:{address},IP:127.0.0.1\n"
                )
            subprocess.run(
                [
                    openssl_bin,
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-keyout",
                    key_path,
                    "-out",
                    cert_path,
                    "-days",
                    "1",
                    "-nodes",
                    "-config",
                    cnf_path,
                    "-extensions",
                    "v3",
                ],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                [
                    openssl_bin,
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-keyout",
                    enc_key_path,
                    "-out",
                    enc_cert_path,
                    "-days",
                    "1",
                    "-passout",
                    "pass:secret",
                    "-config",
                    cnf_path,
                    "-extensions",
                    "v3",
                ],
                check=True,
                capture_output=True,
            )
            seen_file = os.path.join(folder, "seen.json")
            phase1_file = seen_file + ".phase1"
            phase2_go = os.path.join(folder, "phase2_go")
            phase2_file = seen_file + ".phase2"
            phase3_go = os.path.join(folder, "phase3_go")
            phase3_file = seen_file + ".phase3"
            finish_go = os.path.join(folder, "finish_go")

            mcp.run(
                _token_and_tls_flow(
                    address,
                    cert_path,
                    key_path,
                    enc_key_path,
                    seen_file,
                    phase1_file,
                    phase2_go,
                    phase2_file,
                    phase3_go,
                    phase3_file,
                    finish_go,
                )
            )
            self.assertTrue(
                wait_for_file(phase1_file),
                "the bridge did not reach the TLS + token phase-1 state",
            )
            with open(phase1_file, encoding="utf-8") as handle:
                p1 = json.load(handle)

            token1 = p1["token1"]
            https_url = f"https://{address}:{PORT}/mcp"
            http_url = f"http://{address}:{PORT}/mcp"
            client_ctx = ssl.create_default_context(cafile=cert_path)

            # Plain HTTP to the TLS non-loopback entry fails.
            with self.assertRaises(Exception):
                _post_mcp(http_url, "print('plain http')", ssl_context=None)

            # HTTPS without a bearer token gets 401 Unauthorized with
            # WWW-Authenticate: Bearer.
            status_no_tok, auth_no_tok = _post_mcp(
                https_url,
                "print('no token')",
                ssl_context=client_ctx,
                token=None,
            )
            self.assertEqual(status_no_tok, 401)
            self.assertEqual(auth_no_tok, "Bearer")

            # HTTPS with the wrong bearer token gets 401 Unauthorized with
            # WWW-Authenticate: Bearer.
            status_bad_tok, auth_bad_tok = _post_mcp(
                https_url,
                "print('bad token')",
                ssl_context=client_ctx,
                token=token1 + "wrong",
            )
            self.assertEqual(status_bad_tok, 401)
            self.assertEqual(auth_bad_tok, "Bearer")

            # HTTPS with token1 succeeds.
            status_ok1, out1 = _post_mcp(
                https_url,
                "print('https with token1')",
                ssl_context=client_ctx,
                token=token1,
            )
            self.assertEqual(status_ok1, 200)
            self.assertEqual(out1, "https with token1\n")

            # A client that connects to the TLS entry and sends nothing does
            # not stop a second client from being served.
            with socket.create_connection((address, PORT), timeout=5) as idle:
                status_while_idle, out_while_idle = _post_mcp(
                    https_url,
                    "print('served while idle client connected')",
                    ssl_context=client_ctx,
                    token=token1,
                )
                self.assertEqual(status_while_idle, 200)
                self.assertEqual(
                    out_while_idle, "served while idle client connected\n"
                )
                _ = idle

            # Loopback is HTTP and still requires the token. The client
            # reads it from the token file. A request with no token is
            # refused. The shim reads the same file, with nothing pasted.
            loopback_url = f"http://127.0.0.1:{PORT}/mcp"
            status_loop_no, auth_loop_no = _post_mcp(
                loopback_url,
                "print('loopback no token')",
                token=None,
            )
            self.assertEqual(status_loop_no, 401)
            self.assertEqual(auth_loop_no, "Bearer")
            loopback_out = mcp.run("print('loopback unchanged')")
            self.assertEqual(loopback_out, "loopback unchanged\n")
            node_bin = shutil.which("node")
            if node_bin is not None:
                shim_out = _run_via_stdio_shim(
                    node_bin, PORT, "print('desktop shim unchanged')"
                )
                self.assertEqual(shim_out, "desktop shim unchanged\n")

            # Trigger phase 2: regenerate the token via Preferences UI and
            # restart to verify the token dialog is not shown again on start.
            open(phase2_go, "w", encoding="utf-8").close()
            self.assertTrue(
                wait_for_file(phase2_file),
                "the bridge did not finish regenerating the token",
            )
            with open(phase2_file, encoding="utf-8") as handle:
                p2 = json.load(handle)
            token2 = p2["token2"]
            self.assertNotEqual(token1, token2)

            # Old token is now refused with 401 and WWW-Authenticate: Bearer;
            # new token succeeds with 200.
            status_old, auth_old = _post_mcp(
                https_url,
                "print('old token')",
                ssl_context=client_ctx,
                token=token1,
            )
            self.assertEqual(status_old, 401)
            self.assertEqual(auth_old, "Bearer")
            status_new, out2 = _post_mcp(
                https_url,
                "print('https with token2')",
                ssl_context=client_ctx,
                token=token2,
            )
            self.assertEqual(status_new, 200)
            self.assertEqual(out2, "https with token2\n")

            # Trigger phase 3: the old Apply-to-loopback preference
            # does not turn loopback into HTTPS.
            open(phase3_go, "w", encoding="utf-8").close()
            self.assertTrue(
                wait_for_file(phase3_file),
                "the bridge did not reach the loopback TLS + token "
                "phase-3 state",
            )
            with open(phase3_file, encoding="utf-8") as handle:
                p3 = json.load(handle)
            self.assertTrue(p3.get("loopback_started_running"))
            loopback_http_url = f"http://127.0.0.1:{PORT}/mcp"
            loopback_https_url = f"https://127.0.0.1:{PORT}/mcp"

            # Loopback stays HTTP. The token is still required.
            lb_no_tok, lb_auth_no_tok = _post_mcp(
                loopback_http_url,
                "print('loopback no token')",
                token=None,
            )
            self.assertEqual(lb_no_tok, 401)
            self.assertEqual(lb_auth_no_tok, "Bearer")
            lb_ok, lb_out = _post_mcp(
                loopback_http_url,
                "print('loopback http with token2')",
                token=token2,
            )
            self.assertEqual(lb_ok, 200)
            self.assertEqual(lb_out, "loopback http with token2\n")
            with self.assertRaises(Exception):
                _post_mcp(
                    loopback_https_url,
                    "print('loopback https')",
                    ssl_context=client_ctx,
                    token=token2,
                )

            open(finish_go, "w", encoding="utf-8").close()
            self.assertTrue(
                wait_for_file(seen_file),
                "the restore step did not finish",
            )
            self.assertTrue(
                mcp.wait_until_reachable(30), "the bridge did not come back"
            )
            with open(seen_file, encoding="utf-8") as handle:
                seen = json.load(handle)

        # 1. With TLS on and no certificate or an encrypted key, start failed
        # and left the bridge off.
        self.assertFalse(seen.get("no_cert_running"))
        self.assertIn(
            "TLS is required and needs a certificate file and a private "
            "key file.",
            seen.get("no_cert_status", ""),
        )
        self.assertIn(
            "untick Require TLS there.", seen.get("no_cert_report", "")
        )
        self.assertFalse(seen.get("enc_key_running"))
        self.assertIn(
            "Could not load TLS certificate or private key",
            seen.get("enc_key_status", ""),
        )
        self.assertIn(
            "untick Require TLS in Edit", seen.get("enc_key_report", "")
        )

        # 2. Risk dialog listed only the remaining risk (code execution with
        # the bearer token), not No authentication or No encryption.
        self.assertTrue(seen.get("risk_dialog_found"))
        risk_text = seen.get("risk_dialog_text", "")
        self.assertNotIn("No authentication", risk_text)
        self.assertNotIn("No encryption", risk_text)
        self.assertIn(
            "Code execution: any client with the bearer token", risk_text
        )

        # 3. Token dialog shown once, names the token file, and is not
        # shown again on restart. Generate new token shows it again.
        self.assertTrue(seen.get("token1_dialog_found"))
        self.assertIn(
            "readable only by you",
            seen.get("token1_dialog_text", ""),
        )
        self.assertIn(
            "The Claude Desktop extension reads it from that file.",
            seen.get("token1_dialog_text", ""),
        )
        self.assertIn(
            "Other clients send it in the Authorization header:",
            seen.get("token1_dialog_text", ""),
        )
        self.assertNotIn("one-way hash", seen.get("token1_dialog_text", ""))
        self.assertTrue(seen.get("token_file_matches_dialog"))
        self.assertFalse(seen.get("restart_token_dialog_found"))
        self.assertTrue(seen.get("regen_token_dialog_found"))

        # 4. user.cfg holds neither raw token nor a new hash.
        self.assertEqual(seen.get("token1_hash_len"), 0)
        self.assertFalse(seen.get("user_cfg_contains_token1"))
        self.assertFalse(seen.get("user_cfg_contains_token2"))
        self.assertFalse(seen.get("user_cfg_contains_hash2"))

        # 5. Status bar and Report view warning for both on.
        expected_status = (
            f"MCP Bridge: Listening on 127.0.0.1:{PORT}, "
            f"{address}:{PORT} (warning: connected agents can run "
            "code with your full permissions; beyond loopback: "
            f"{address}:{PORT})"
        )
        expected_warning = (
            f"[MCP Bridge] WARNING: listening on 127.0.0.1:{PORT}, "
            f"{address}:{PORT}. TLS is required beyond loopback, and "
            "the bearer token everywhere. Any client with the bearer "
            "token that can reach "
            "these addresses can run Python code in FreeCAD as your user "
            f"account: {address}:{PORT}."
        )
        self.assertEqual(seen.get("started_status"), expected_status)
        self.assertIn(expected_warning, seen.get("started_report", ""))

        # 6. The old Apply-to-loopback preference does not make loopback
        # need a certificate. The start stays HTTP, shows no risk dialog
        # and no new token dialog, and the status names the permissions.
        self.assertFalse(seen.get("loopback_risk_dialog_found"))
        self.assertFalse(seen.get("loopback_token_dialog_found"))
        self.assertTrue(seen.get("loopback_started_running"))
        self.assertIn(
            "full permissions",
            seen.get("loopback_started_status", ""),
        )
        self.assertNotIn(
            "beyond loopback",
            seen.get("loopback_started_status", ""),
        )


def _post_mcp(url, code, ssl_context=None, token=None):
    """Send an MCP 2025-11-25 tools/call execute_python POST and return
    `(http_status, stdout_text)`."""
    payload = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "execute_python",
                "arguments": {"code": code},
            },
        }
    ).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "MCP-Protocol-Version": "2025-11-25",
    }
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        url, data=payload, headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(
            req, context=ssl_context, timeout=10
        ) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            text = json.loads(body["result"]["content"][0]["text"])
            out = "".join(
                chunk["text"]
                for chunk in text.get("page", [])
                if chunk.get("stream") == "stdout"
            )
            return resp.status, out
    except urllib.error.HTTPError as err:
        return err.code, err.headers.get("WWW-Authenticate", "")


def _run_via_stdio_shim(
    node_bin, port, code, token=None, tls_cert_file=None
):
    """Run `code` through `mcp-stdio-shim/index.js` over stdio against
    `127.0.0.1:port` and return stdout text."""
    shim_js = os.path.abspath(
        os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "mcp-stdio-shim",
            "index.js",
        )
    )
    env = os.environ.copy()
    env["FREECAD_MCP_PORT"] = str(port)
    if token is not None:
        env["FREECAD_MCP_TOKEN"] = token
    else:
        env.pop("FREECAD_MCP_TOKEN", None)
    if tls_cert_file is not None:
        env["FREECAD_MCP_TLS_CERT_FILE"] = tls_cert_file
    else:
        env.pop("FREECAD_MCP_TLS_CERT_FILE", None)
    msg = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "execute_python",
                "arguments": {"code": code},
            },
        }
    )
    proc = subprocess.Popen(
        [node_bin, shim_js],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    try:
        proc.stdin.write((msg + "\n").encode("utf-8"))
        proc.stdin.flush()
        raw_line = proc.stdout.readline()
        proc.stdin.close()
        proc.wait(timeout=5)
    finally:
        if proc.poll() is None:
            proc.kill()
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None and not stream.closed:
                stream.close()
    reply = json.loads(raw_line.decode("utf-8").strip())
    payload = json.loads(reply["result"]["content"][0]["text"])
    return "".join(
        chunk["text"]
        for chunk in payload.get("page", [])
        if chunk.get("stream") == "stdout"
    )


def _token_and_tls_flow(
    address,
    cert_path,
    key_path,
    enc_key_path,
    seen_file,
    phase1_file,
    phase2_go,
    phase2_file,
    phase3_go,
    phase3_file,
    finish_go,
):
    entry = f"{address}:{PORT}"
    loopback_entry = f"127.0.0.1:{PORT}"
    return PREFERENCES + f"""
import json
import os
import re
from PySide.QtWidgets import (
    QApplication,
    QDialog,
    QDockWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTextEdit,
)

seen = {{}}
mw = Gui.getMainWindow()
dock = mw.findChild(QDockWidget, 'Report view')
was_visible = dock is not None and dock.isVisible()
orig_addr = params.GetString('ListenAddress', '')
orig_accepted = params.GetString('AcceptedListenAddresses', '')
orig_req_token = params.GetBool('RequireToken', True)
orig_req_tls = params.GetBool('RequireTls', True)
orig_req_on_loopback = params.GetBool('RequireOnLoopback', False)
orig_token_hash = params.GetString('TokenHash', '')
orig_cert = params.GetString('TlsCertFile', '')
orig_key = params.GetString('TlsKeyFile', '')


def report_view_text():
    if dock is None:
        return ''
    dock.show()
    QApplication.processEvents()
    for kind in (QTextEdit, QPlainTextEdit):
        for view in dock.findChildren(kind):
            return view.toPlainText()
    return ''


def extract_token(text):
    for line in (text or '').splitlines():
        if line.startswith('Authorization: Bearer '):
            return line.split('Authorization: Bearer ', 1)[1].strip()
    return ''


def handle_dialogs(risk_key, token_key):
    risk_dlg = None
    for name in ('MCPBridgeFirstStartDialog', 'MCPBridgeRiskDialog'):
        found = mw.findChild(QMessageBox, name)
        if found is not None and found.isVisible():
            risk_dlg = found
            break
    if risk_dlg is not None:
        seen[risk_key + '_found'] = True
        seen[risk_key + '_text'] = risk_dlg.text()
        QTimer.singleShot(
            150, lambda: handle_dialogs(risk_key, token_key)
        )
        accept_label = (
            'Accept and Start'
            if risk_dlg.objectName() == 'MCPBridgeFirstStartDialog'
            else 'Accept Risks and Start'
        )
        for btn in risk_dlg.buttons():
            if btn.text() == accept_label:
                btn.click()
                return
    tok_dlg = None
    for box in mw.findChildren(QMessageBox):
        if box.objectName() == 'MCPBridgeTokenDialog' and box.isVisible():
            tok_dlg = box
            break
    if tok_dlg is not None:
        seen[token_key + '_found'] = True
        seen[token_key + '_text'] = tok_dlg.text()
        seen[token_key + '_value'] = extract_token(tok_dlg.text())
        for btn in tok_dlg.buttons():
            if btn.text() == 'OK':
                btn.click()
                return


def step_no_cert_then_start():
    if bridge.is_running():
        action.trigger()
    params.SetString(
        'ListenAddress', {loopback_entry!r} + '\\n' + {entry!r}
    )
    params.RemString('AcceptedListenAddresses')
    params.RemString('TokenHash')
    params.RemBool('RequireToken')
    params.RemBool('RequireTls')
    params.RemBool('RequireOnLoopback')
    params.RemString('TlsCertFile')
    params.RemString('TlsKeyFile')
    before_no_cert = len(report_view_text())
    action.trigger()
    seen['no_cert_running'] = bridge.is_running()
    seen['no_cert_status'] = mw.statusBar().currentMessage()
    seen['no_cert_report'] = report_view_text()[before_no_cert:]

    # A passphrase-protected private key fails with the TLS load error
    # instead of prompting on a console FreeCAD does not have.
    params.SetString('TlsCertFile', {cert_path!r})
    params.SetString('TlsKeyFile', {enc_key_path!r})
    before_enc_key = len(report_view_text())
    action.trigger()
    seen['enc_key_running'] = bridge.is_running()
    seen['enc_key_status'] = mw.statusBar().currentMessage()
    seen['enc_key_report'] = report_view_text()[before_enc_key:]

    # Now set the throwaway cert and key and start with defaults.
    # The old RequireToken preference is unread. Clear the token file
    # so this start shows the token once.
    params.SetString('TlsCertFile', {cert_path!r})
    params.SetString('TlsKeyFile', {key_path!r})
    from freecad.mcp_bridge import token_file as token_store
    token_path = str(token_store.token_path())
    if os.path.exists(token_path):
        os.remove(token_path)
    seen['risk_dialog_found'] = False
    seen['token1_dialog_found'] = False
    before_start = len(report_view_text())
    QTimer.singleShot(
        150, lambda: handle_dialogs('risk_dialog', 'token1_dialog')
    )
    action.trigger()
    seen['started_running'] = bridge.is_running()
    seen['started_status'] = mw.statusBar().currentMessage()
    seen['started_report'] = report_view_text()[before_start:]
    seen['token1'] = seen.get('token1_dialog_value', '')
    seen['token1_hash'] = params.GetString('TokenHash', '')
    seen['token1_hash_len'] = len(seen['token1_hash'])
    file_token = ''
    if os.path.exists(token_path):
        with open(token_path, encoding='utf-8') as token_file:
            file_token = token_file.read().strip()
    seen['token_file_matches_dialog'] = (
        bool(seen['token1']) and file_token == seen['token1']
    )
    with open({phase1_file!r}, 'w', encoding='utf-8') as handle:
        json.dump(seen, handle)
    QTimer.singleShot(200, wait_for_phase2)


def wait_for_phase2():
    if not os.path.exists({phase2_go!r}):
        QTimer.singleShot(200, wait_for_phase2)
        return
    # Restart the bridge and verify the token dialog is NOT shown again.
    if bridge.is_running():
        action.trigger()
    seen['restart_token_dialog_found'] = False
    QTimer.singleShot(
        150,
        lambda: handle_dialogs('restart_risk_dialog', 'restart_token_dialog'),
    )
    action.trigger()
    seen['restarted_running'] = bridge.is_running()

    # Open Preferences and click Generate new token.
    seen['regen_token_dialog_found'] = False

    def click_generate_in_prefs():
        dlg = next(
            (
                d
                for d in mw.findChildren(QDialog)
                if d.findChild(QPushButton, 'generateTokenButton') is not None
            ),
            None,
        )
        if dlg is None:
            QTimer.singleShot(100, click_generate_in_prefs)
            return
        btn = dlg.findChild(QPushButton, 'generateTokenButton')

        def close_token_and_prefs():
            for box in dlg.findChildren(QMessageBox) + mw.findChildren(
                QMessageBox
            ):
                if (
                    box.objectName() == 'MCPBridgeTokenDialog'
                    and box.isVisible()
                ):
                    seen['regen_token_dialog_found'] = True
                    seen['token2'] = extract_token(box.text())
                    for b in box.buttons():
                        if b.text() == 'OK':
                            b.click()
                            break
                    break
            QTimer.singleShot(100, dlg.accept)

        QTimer.singleShot(150, close_token_and_prefs)
        btn.click()

    QTimer.singleShot(250, click_generate_in_prefs)
    Gui.showPreferences('MCP Bridge', 0)
    App.saveParameter()
    user_cfg_path = App.ConfigGet('UserParameter')
    cfg_text = ''
    if os.path.exists(user_cfg_path):
        with open(user_cfg_path, encoding='utf-8', errors='ignore') as cf:
            cfg_text = cf.read()
    seen['token2_hash'] = params.GetString('TokenHash', '')
    seen['user_cfg_contains_token1'] = bool(
        seen.get('token1') and seen['token1'] in cfg_text
    )
    seen['user_cfg_contains_token2'] = bool(
        seen.get('token2') and seen['token2'] in cfg_text
    )
    seen['user_cfg_contains_hash2'] = bool(
        seen['token2_hash'] and seen['token2_hash'] in cfg_text
    )
    with open({phase2_file!r}, 'w', encoding='utf-8') as handle:
        json.dump(seen, handle)
    QTimer.singleShot(200, wait_for_phase3)


def wait_for_phase3():
    if not os.path.exists({phase3_go!r}):
        QTimer.singleShot(200, wait_for_phase3)
        return
    if bridge.is_running():
        action.trigger()
    params.SetString('ListenAddress', {loopback_entry!r})
    params.SetBool('RequireOnLoopback', True)
    params.RemString('TlsCertFile')
    params.RemString('TlsKeyFile')
    seen['loopback_risk_dialog_found'] = False
    seen['loopback_token_dialog_found'] = False
    QTimer.singleShot(
        150,
        lambda: handle_dialogs(
            'loopback_risk_dialog', 'loopback_token_dialog'
        ),
    )
    action.trigger()
    seen['loopback_started_running'] = bridge.is_running()
    seen['loopback_started_status'] = mw.statusBar().currentMessage()
    with open({phase3_file!r}, 'w', encoding='utf-8') as handle:
        json.dump(seen, handle)
    QTimer.singleShot(200, wait_for_finish)


def wait_for_finish():
    if not os.path.exists({finish_go!r}):
        QTimer.singleShot(200, wait_for_finish)
        return
    if bridge.is_running():
        action.trigger()
    if orig_addr:
        params.SetString('ListenAddress', orig_addr)
    else:
        params.RemString('ListenAddress')
    if orig_accepted:
        params.SetString('AcceptedListenAddresses', orig_accepted)
    else:
        params.RemString('AcceptedListenAddresses')
    params.SetBool('RequireToken', orig_req_token)
    params.SetBool('RequireTls', orig_req_tls)
    params.SetBool('RequireOnLoopback', orig_req_on_loopback)
    if orig_token_hash:
        params.SetString('TokenHash', orig_token_hash)
    else:
        params.RemString('TokenHash')
    if orig_cert:
        params.SetString('TlsCertFile', orig_cert)
    else:
        params.RemString('TlsCertFile')
    if orig_key:
        params.SetString('TlsKeyFile', orig_key)
    else:
        params.RemString('TlsKeyFile')
    if dock is not None and not was_visible:
        dock.hide()
    action.trigger()
    with open({seen_file!r}, 'w', encoding='utf-8') as handle:
        json.dump(seen, handle)


QTimer.singleShot(300, step_no_cert_then_start)
"""



if __name__ == "__main__":
    unittest.main()
