"""Verify addon startup after a fresh FreeCAD restart.

Run inside a FreeCAD GUI process (second launch, after test_install.py):

  freecad scripts/test_verify.py

Does not call App.MCPBridgeInjectUi(); waits for the addon's own startup hooks.

Environment:
  RELEASE_INSTALL_NAME              Installed Mod folder name (default: freecad-mcp-bridge)
  RELEASE_INSTALL_PROBE             Python sent over HTTP to execute_python
  RELEASE_INSTALL_PROBE_EXPECT      Expected substring in the exec output
  RELEASE_INSTALL_GUI_DELAY_MS      Delay before work starts (default: 3000)
  RELEASE_INSTALL_TOOLBAR_TIMEOUT_MS  Wait for auto-injected toolbar (default: 15000)
  RELEASE_INSTALL_HTTP_TIMEOUT_MS     HTTP exec round-trip wait (default: 30000)
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


_PREFS_LINE = (
    "To start or stop the bridge, click <b>MCP Bridge On/Off</b> "
    "in the <b>MCP Bridge</b> toolbar. While active, it listens "
    "on the port configured below."
)


def _verify_preferences_line() -> None:
    """The installed Preferences page carries the switch-on line."""
    from freecad.mcp_bridge.resources import preferences_ui_path

    path = preferences_ui_path()
    try:
        raw = open(path, encoding="utf-8").read()
    except OSError as exc:
        common.fail(f"Preferences page is missing ({path}): {exc}")
    text = raw.replace("&lt;", "<").replace("&gt;", ">")
    if _PREFS_LINE not in text:
        common.fail(
            "Preferences page is missing the switch-on line. "
            f"Looked in {path}"
        )
    common.log_notice("Preferences page has the switch-on line")


def _status_button():
    """The permanent status-bar button, or None if it was not added."""
    import FreeCADGui as Gui
    from PySide.QtWidgets import QToolButton

    from freecad.mcp_bridge.constants import STATUS_BUTTON_OBJECT_NAME

    mw = Gui.getMainWindow()
    if mw is None:
        return None
    return mw.statusBar().findChild(
        QToolButton, STATUS_BUTTON_OBJECT_NAME
    )


def _assert_status_button(running: bool) -> None:
    """The status-bar button matches the bridge, and so does the toolbar."""
    import FreeCAD as App
    import FreeCADGui as Gui
    from PySide.QtWidgets import QToolButton

    from freecad.mcp_bridge.init_gui import _status_bar_widgets

    mw = Gui.getMainWindow()
    button = _status_button()
    if mw is None or button is None or not button.isVisible():
        common.fail("Status-bar button is missing or hidden")
    _, permanent = _status_bar_widgets(mw.statusBar())
    if permanent is None:
        common.fail("Status-bar widget layout is unavailable")
    if len(permanent) < 2 or permanent[-1] is not button:
        names = [w.objectName() or type(w).__name__ for w in permanent]
        common.fail(
            "Status-bar button is not to the right of FreeCAD's permanent "
            f"widgets (permanent={names})"
        )
    expected = "MCP On" if running else "MCP Off"
    if button.text() != expected:
        common.fail(
            "Status-bar button text is out of step. "
            f"Expected {expected!r}, got {button.text()!r}"
        )
    if not button.icon().isNull():
        common.fail("Status-bar button has an icon")
    if button.popupMode() != QToolButton.MenuButtonPopup:
        common.fail(
            "Status-bar button is not in MenuButtonPopup mode "
            f"(popupMode={button.popupMode()!r})"
        )
    menu = button.menu()
    if menu is None or len(menu.actions()) != 1:
        count = None if menu is None else len(menu.actions())
        common.fail(
            "Status-bar button menu is missing or does not have 1 action "
            f"(count={count})"
        )
    if menu.actions()[0].text() != "Preferences":
        common.fail(
            "Status-bar button menu action text is unexpected: "
            f"{menu.actions()[0].text()!r}"
        )
    tip = (
        "MCP Bridge: on (click to stop)"
        if running
        else "MCP Bridge: off (click to start)"
    )
    if button.toolTip() != tip:
        common.fail(
            "Status-bar button tooltip is out of step. "
            f"Expected {tip!r}, got {button.toolTip()!r}"
        )
    action = getattr(App, "MCPBridgeAction", None)
    pressed = None if action is None else action.isChecked()
    if pressed != running:
        common.fail(
            "Toolbar button is out of step with the status-bar button "
            f"(pressed={pressed}, bridge running={running})"
        )


def _verify_status_button_hide() -> None:
    """Status-bar preferences move and hide the button without restart."""
    import FreeCAD as App
    import FreeCADGui as Gui

    from freecad.mcp_bridge.constants import PREF_GROUP, STATUS_BUTTON_LEFT
    from freecad.mcp_bridge.init_gui import _status_bar_widgets

    mw = Gui.getMainWindow()
    params = App.ParamGet(PREF_GROUP)
    params.SetInt("StatusButtonPosition", STATUS_BUTTON_LEFT)
    try:
        button = _status_button()
        _, permanent = (
            _status_bar_widgets(mw.statusBar()) if mw else (None, None)
        )
        if permanent is None:
            common.fail(
                "Status-bar widget layout is unavailable with "
                "StatusButtonPosition=STATUS_BUTTON_LEFT"
            )
        if (
            button is None
            or len(permanent) < 2
            or permanent[0] is not button
        ):
            names = [w.objectName() or type(w).__name__ for w in permanent]
            common.fail(
                "Status-bar button is not first among permanent widgets "
                "with StatusButtonPosition=STATUS_BUTTON_LEFT "
                f"(permanent={names})"
            )
    finally:
        params.RemInt("StatusButtonPosition")
    _assert_status_button(False)

    params.SetBool("ShowStatusButton", False)
    try:
        if _status_button() is not None:
            common.fail(
                "Status-bar button remained present with "
                "ShowStatusButton=False"
            )
        if getattr(App, "MCPBridgeAction", None) is None:
            common.fail(
                "Toolbar action disappeared when status-bar button was hidden"
            )
    finally:
        params.RemBool("ShowStatusButton")
    _assert_status_button(False)
    common.log_notice(
        "Status-bar button moves, hides and returns on preference change"
    )


def _stop_bridge() -> None:
    """Stop the server via the status-bar button, like a user would."""
    from PySide.QtCore import QCoreApplication, QEvent, QPointF, Qt
    from PySide.QtGui import QMouseEvent
    from PySide.QtWidgets import QStyle, QStyleOptionToolButton

    from freecad.mcp_bridge import bridge
    from freecad.mcp_bridge.constants import DISPLAY_NAME, LOG_PREFIX

    if not bridge.is_running():
        common.fail("Bridge HTTP server was not running before stop toggle")

    button = _status_button()
    if button is None:
        common.fail("Status-bar button is missing")
    common.log('Clicking status-bar button "MCP On" text (stop)')
    opt = QStyleOptionToolButton()
    button.initStyleOption(opt)
    menu_rect = button.style().subControlRect(
        QStyle.CC_ToolButton, opt, QStyle.SC_ToolButtonMenu, button
    )
    text_rect = button.rect().adjusted(0, 0, -menu_rect.width(), 0)
    pos = QPointF(text_rect.center())
    for event_type, buttons in (
        (QEvent.MouseButtonPress, Qt.LeftButton),
        (QEvent.MouseButtonRelease, Qt.NoButton),
    ):
        QCoreApplication.sendEvent(
            button,
            QMouseEvent(
                event_type,
                pos,
                Qt.LeftButton,
                buttons,
                Qt.NoModifier,
            ),
        )
    QCoreApplication.processEvents()
    common._drain_qt_events(1500)

    if bridge.is_running():
        common.fail(
            "Bridge HTTP server still running after the status-bar click"
        )

    expected = common.env(
        "RELEASE_INSTALL_STOP_MESSAGE",
        f"{LOG_PREFIX} HTTP server stopped",
    )
    report_after = _report_view_text()
    if expected not in report_after:
        common.fail(
            "Stop toggle did not print the expected Report view message. "
            f"Expected substring {expected!r} in report view."
        )

    import FreeCADGui as Gui

    mw = Gui.getMainWindow()
    expected_status = f"{DISPLAY_NAME}: Offline"
    status_message = mw.statusBar().currentMessage() if mw else ""
    if expected_status not in status_message:
        common.fail(
            "Stop toggle did not update the status bar. "
            f"Expected substring {expected_status!r}, got: {status_message!r}"
        )

    _assert_status_button(False)
    common.log_notice(
        "Bridge stopped via the status-bar button "
        f"(saw {expected!r} in Report view)"
    )


def _http_exec_probe(code: str, timeout_ms: int) -> dict:
    """Run `code` via tools/call over HTTP.

    The request runs on a worker thread while this (main) thread pumps the Qt
    event loop, so the queued exec can run on the main thread without deadlock.
    Returns {"output": str} or {"error": str}.
    """
    import json
    import threading
    import time
    import urllib.request

    from PySide.QtCore import QCoreApplication, QThread

    from freecad.mcp_bridge import config
    from freecad.mcp_bridge.constants import PROTOCOL_VERSION

    url = f"http://127.0.0.1:{config.port()}/mcp"
    timeout_s = timeout_ms / 1000.0
    result: dict = {}

    def _post(payload):
        # A valid request in the bridge's protocol version: version, client
        # info and capabilities in _meta, mirrored in the headers.
        params = payload["params"]
        params["_meta"] = {
            "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
            "io.modelcontextprotocol/clientInfo": {
                "name": "test-verify",
                "version": "0",
            },
            "io.modelcontextprotocol/clientCapabilities": {},
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": PROTOCOL_VERSION,
                "Mcp-Method": payload["method"],
                "Mcp-Name": params["name"],
            },
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = resp.read().decode("utf-8")
        # Plain JSON; a release older than 2026-07-28 answers over SSE.
        for line in body.splitlines():
            if line.startswith("data:"):
                body = line[len("data:"):].strip()
                break
        envelope = json.loads(body)
        return json.loads(envelope["result"]["content"][0]["text"])

    def worker():
        try:
            page = _post(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "execute_python", "arguments": {"code": code}},
                }
            )
            output = "".join(
                chunk.get("text", "") for chunk in page.get("page", [])
            )
            last_page_no = page.get("page_no")
            req_id = 2
            while page.get("has_more"):
                next_no = 0 if last_page_no is None else last_page_no + 1
                page = _post(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "method": "tools/call",
                        "params": {
                            "name": "get_output_page",
                            "arguments": {
                                "job_token": page["job_token"],
                                "page_no": next_no,
                            },
                        },
                    }
                )
                output += "".join(
                    chunk.get("text", "") for chunk in page.get("page", [])
                )
                if page.get("page_no") is not None:
                    last_page_no = page["page_no"]
                req_id += 1
            result["output"] = output
        except Exception as exc:  # noqa: BLE001 - reported to the caller
            result["error"] = str(exc)

    thread = threading.Thread(target=worker, name="verify-http-probe")
    thread.start()
    deadline = time.monotonic() + timeout_s + 5
    while thread.is_alive() and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        QThread.msleep(20)
    thread.join(timeout=2)
    return result


def _verify_http_round_trip() -> None:
    """Run Python through the HTTP endpoint (same path as send_cmd.py)."""
    probe = common.env("RELEASE_INSTALL_PROBE", 'print("test_verify_ok")')
    expected = common.env("RELEASE_INSTALL_PROBE_EXPECT", "test_verify_ok")
    timeout_ms = int(common.env("RELEASE_INSTALL_HTTP_TIMEOUT_MS", "30000"))

    result = _http_exec_probe(probe, timeout_ms)

    if "error" in result:
        common.fail(f"HTTP exec round-trip failed: {result['error']}")

    output = result.get("output", "")
    if not output:
        common.fail("Timed out waiting for HTTP exec response")

    if expected not in output:
        common.fail(
            "Unexpected exec output. "
            f"Expected substring {expected!r}, got: {output!r}"
        )

    common.log_notice(f"HTTP exec round-trip OK (saw {expected!r})")


def _run() -> None:
    import FreeCAD as App

    common.log_group_start("Verify addon after restart")
    common.log(
        "Verify phase after restart "
        f"(user_data={App.getUserAppDataDir()})"
    )
    common.log_opengl_info()

    _verify_addon_startup()
    _verify_preferences_line()
    common.check_bridge_off_at_startup()
    _assert_status_button(False)
    _verify_status_button_hide()
    common.start_bridge()
    _assert_status_button(True)
    common.check_listening_status()
    _verify_http_round_trip()
    _stop_bridge()
    common.log_notice("Verify passed")
    common.log_group_end()
    common.quit_freecad(0)


common.LOG_PREFIX = "[test_verify]"
common.schedule_main(_run)
