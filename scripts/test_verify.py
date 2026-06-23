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

    common.log(f"Addon command {command_id!r} registered on startup")


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


def _start_bridge_listener() -> None:
    """Start the listener by triggering the real toolbar button."""
    from PySide.QtCore import QCoreApplication
    from freecad.mcp_bridge import bridge as bridge_mod

    inst = bridge_mod._bridge_instance
    if inst and inst.isListening():
        common.log("Bridge listener already running")
        return

    action = _find_toolbar_action()
    if action is None:
        common.fail(
            'Toolbar action "MCP Bridge On/Off" not found after startup '
            "(UI was not auto-injected)"
        )

    common.log('Triggering toolbar action "MCP Bridge On/Off"')
    action.trigger()
    QCoreApplication.processEvents()

    inst = bridge_mod._bridge_instance
    if not inst or not inst.isListening():
        common.fail("Bridge listener failed to start after toolbar click")


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

    common.log(f"Socket round-trip OK (saw {expected!r})")


def _run() -> None:
    import FreeCAD as App

    common.log(
        "Verify phase after restart "
        f"(user_data={App.getUserAppDataDir()})"
    )

    _verify_addon_startup()
    _start_bridge_listener()
    _verify_socket_round_trip()
    common.log("Verify passed")
    common.quit_freecad(0)


common.LOG_PREFIX = "[test_verify]"
common.schedule_main(_run)