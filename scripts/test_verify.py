"""Verify addon startup after a fresh FreeCAD restart.

Run inside a FreeCAD GUI process (second launch, after test_install.py):

  freecad scripts/test_verify.py

Does not call App.MCPBridgeInjectUi(); waits for the addon's own startup hooks.

Environment:
  RELEASE_INSTALL_NAME              Installed Mod folder name (default: freecad-mcp-bridge)
  RELEASE_INSTALL_PROBE             Python sent over the local socket
  RELEASE_INSTALL_PROBE_EXPECT      Expected substring in socket response
  RELEASE_INSTALL_GUI_DELAY_MS      Delay before work starts (default: 3000)
  RELEASE_INSTALL_TOOLBAR_TIMEOUT_MS  Wait for auto-injected toolbar (default: 15000)
  RELEASE_INSTALL_SOCKET_TIMEOUT_MS   Socket response wait (default: 30000)
  RELEASE_INSTALL_STOP_MESSAGE        Expected Report-view line after toggle off
"""

from __future__ import annotations

import os
import sys

_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

import test_install_common as common


def _verify_addon_startup() -> None:
    """Confirm FreeCAD loaded and registered the addon without manual injection."""
    import FreeCADGui as Gui

    command_id = "MCP_Bridge_Toggle"

    if command_id not in Gui.listCommands():
        common.fail(
            f"Addon command {command_id!r} was not registered on startup "
            "(init_gui did not run automatically)"
        )

    if not os.path.isdir(common.installed_addon_dir()):
        common.fail("Installed addon directory is missing after restart")

    common.log_notice(f"Addon command {command_id!r} registered on startup")


def _find_toolbar_action():
    import FreeCADGui as Gui
    from PySide.QtCore import QCoreApplication, QThread
    from PySide.QtWidgets import QToolBar

    from freecad.mcp_bridge.constants import DISPLAY_NAME, TOOLBAR_OBJECT_NAME

    action_label = "MCP Bridge On/Off"
    timeout_ms = int(common.env("RELEASE_INSTALL_TOOLBAR_TIMEOUT_MS", "15000"))
    interval_ms = 200
    attempts = max(1, timeout_ms // interval_ms)

    for _ in range(attempts):
        QCoreApplication.processEvents()

        mw = Gui.getMainWindow()
        if mw:
            for toolbar in mw.findChildren(QToolBar):
                if (
                    toolbar.windowTitle() == DISPLAY_NAME
                    or toolbar.objectName() == TOOLBAR_OBJECT_NAME
                ):
                    for action in toolbar.actions():
                        if action.text() == action_label:
                            return action

        QThread.msleep(interval_ms)

    return None


def _trigger_toolbar_toggle(log_message: str) -> None:
    from PySide.QtCore import QCoreApplication

    action = _find_toolbar_action()
    if action is None:
        common.fail(
            'Toolbar action "MCP Bridge On/Off" not found after startup '
            "(UI was not auto-injected)"
        )

    common.log(log_message)
    action.trigger()
    QCoreApplication.processEvents()


def _bridge_instance():
    from freecad.mcp_bridge import bridge as bridge_mod

    inst = bridge_mod._bridge_instance
    if inst:
        return inst

    import __main__

    return getattr(__main__, "_freecad_bridge_instance", None) or getattr(
        __main__, "_bridge_instance", None
    )


def _report_view_text() -> str:
    """Return visible Report view text (where Console.PrintMessage appears)."""
    import FreeCADGui as Gui
    from PySide.QtWidgets import QDockWidget, QPlainTextEdit, QTextBrowser, QTextEdit

    mw = Gui.getMainWindow()
    if mw is None:
        return ""

    dock = mw.findChild(QDockWidget, "Report view")
    if dock is None:
        return ""

    if not dock.isVisible():
        dock.show()

    for widget_type in (QTextEdit, QPlainTextEdit, QTextBrowser):
        for child in dock.findChildren(widget_type):
            if hasattr(child, "toPlainText"):
                text = child.toPlainText()
            else:
                text = child.toText()
            if text:
                return text

    return ""


def _start_bridge_listener() -> None:
    """Start the listener by triggering the real toolbar button."""
    inst = _bridge_instance()
    if inst and inst.isListening():
        common.log("Bridge listener already running")
        return

    _trigger_toolbar_toggle('Triggering toolbar action "MCP Bridge On/Off" (start)')
    common._drain_qt_events(1000)

    inst = _bridge_instance()
    if not inst or not inst.isListening():
        common.fail("Bridge listener failed to start after toolbar click")


def _stop_bridge_listener() -> None:
    """Stop the listener via a second toolbar toggle, like a user would."""
    from freecad.mcp_bridge.constants import DISPLAY_NAME, LOG_PREFIX

    inst = _bridge_instance()
    if not inst or not inst.isListening():
        common.fail("Bridge listener was not running before stop toggle")

    report_before = _report_view_text()
    _trigger_toolbar_toggle(
        'Triggering toolbar action "MCP Bridge On/Off" (stop)'
    )
    common._drain_qt_events(1500)

    inst = _bridge_instance()
    if inst and inst.isListening():
        common.fail("Bridge listener still running after stop toolbar click")

    expected = common.env(
        "RELEASE_INSTALL_STOP_MESSAGE",
        f"{LOG_PREFIX} Stopped socket listener.",
    )
    report_after = _report_view_text()
    if expected not in report_after:
        common.fail(
            "Stop toggle did not print the expected Report view message. "
            f"Expected substring {expected!r} in report view."
        )

    if expected in report_before:
        common.log("Stop message already present in Report view before toggle")

    import FreeCADGui as Gui

    mw = Gui.getMainWindow()
    expected_status = f"{DISPLAY_NAME}: Offline"
    status_message = mw.statusBar().currentMessage() if mw else ""
    if expected_status not in status_message:
        common.fail(
            "Stop toggle did not update the status bar. "
            f"Expected substring {expected_status!r}, got: {status_message!r}"
        )

    common.log_notice(
        f"Bridge stopped via toolbar toggle (saw {expected!r} in Report view)"
    )


def _read_socket_response(socket, timeout_ms: int) -> str:
    from PySide.QtCore import QCoreApplication, QThread

    interval_ms = 100
    elapsed = 0
    chunks: list[bytes] = []

    while elapsed < timeout_ms:
        QCoreApplication.processEvents()
        if socket.bytesAvailable() > 0:
            chunks.append(socket.readAll().data())
            for _ in range(10):
                QCoreApplication.processEvents()
                if socket.waitForReadyRead(50) and socket.bytesAvailable() > 0:
                    chunks.append(socket.readAll().data())
                else:
                    break
            break
        if socket.waitForReadyRead(interval_ms):
            elapsed += interval_ms
            continue
        elapsed += interval_ms
        QThread.msleep(interval_ms)

    return b"".join(chunks).decode("utf-8")


def _verify_socket_round_trip() -> None:
    """Run Python through the local socket (same protocol as send_cmd.py)."""
    from PySide.QtCore import QCoreApplication
    from PySide.QtNetwork import QLocalSocket

    from freecad.mcp_bridge.constants import SOCKET_NAME

    probe = common.env(
        "RELEASE_INSTALL_PROBE",
        'print("test_verify_ok")',
    )
    if not probe.endswith("\n"):
        probe += "\n"
    expected = common.env("RELEASE_INSTALL_PROBE_EXPECT", "test_verify_ok")
    timeout_ms = int(common.env("RELEASE_INSTALL_SOCKET_TIMEOUT_MS", "30000"))

    socket = QLocalSocket()
    socket.connectToServer(SOCKET_NAME)
    if not socket.waitForConnected(5000):
        common.fail(
            "Socket connection failed. Is the bridge listener running? "
            f"({socket.errorString()})"
        )

    socket.write(probe.encode("utf-8"))
    socket.flush()
    socket.waitForBytesWritten(1000)
    QCoreApplication.processEvents()

    response = _read_socket_response(socket, timeout_ms)
    socket.disconnectFromServer()
    QCoreApplication.processEvents()

    if not response:
        common.fail("Timed out waiting for bridge execution response")

    if expected not in response:
        common.fail(
            "Unexpected socket response. "
            f"Expected substring {expected!r}, got: {response!r}"
        )

    common.log_notice(f"Socket round-trip OK (saw {expected!r})")


def _run() -> None:
    import FreeCAD as App

    common.log_group_start("Verify addon after restart")
    common.log(
        "Verify phase after restart "
        f"(user_data={App.getUserAppDataDir()})"
    )

    _verify_addon_startup()
    _start_bridge_listener()
    _verify_socket_round_trip()
    _stop_bridge_listener()
    common.log_notice("Verify passed")
    common.log_group_end()
    common.quit_freecad(0)


common.LOG_PREFIX = "[test_verify]"
common.schedule_main(_run)