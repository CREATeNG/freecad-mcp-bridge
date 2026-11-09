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
import time

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


def _verify_command_category() -> None:
    """Confirm Tools -> Customize lists the command under MCP_Bridge."""
    import FreeCADGui as Gui
    from PySide.QtCore import QCoreApplication, Qt
    from PySide.QtWidgets import (
        QComboBox,
        QDialog,
        QTabWidget,
        QTreeWidget,
    )

    mw = Gui.getMainWindow()
    if mw is None:
        common.fail("MainWindow is None during command-category check")

    Gui.runCommand("Std_DlgCustomize")
    QCoreApplication.processEvents()
    dlg = next(
        (
            d
            for d in mw.findChildren(QDialog)
            if d.findChild(QTabWidget, "Gui__Dialog__TabWidget") is not None
        ),
        None,
    )
    if dlg is None:
        common.fail("Could not open Tools -> Customize dialog")

    try:
        combo = dlg.findChild(QComboBox, "categoryBox")
        tree = dlg.findChild(QTreeWidget, "commandTreeWidget")
        if combo is None or tree is None:
            common.fail(
                "Customize dialog is missing categoryBox or command tree"
            )

        def _commands_for_category(cat_idx: int) -> list[tuple[str, str]]:
            combo.setCurrentIndex(cat_idx)
            combo.activated.emit(cat_idx)
            common._drain_qt_events(250)
            result = []
            for i in range(tree.topLevelItemCount()):
                item = tree.topLevelItem(i)
                raw = item.data(1, Qt.UserRole)
                if isinstance(raw, (bytes, bytearray)):
                    raw = raw.decode("utf-8", errors="replace")
                elif hasattr(raw, "data"):
                    raw = bytes(raw.data()).decode("utf-8", errors="replace")
                result.append((str(raw), item.text(1)))
            return result

        categories = [
            combo.itemData(i, Qt.UserRole) for i in range(combo.count())
        ]
        init_gui_idx = combo.findData("init_gui")
        if init_gui_idx >= 0:
            init_gui_cmds = _commands_for_category(init_gui_idx)
            if any(cmd == "MCP_Bridge_Toggle" for cmd, _ in init_gui_cmds):
                common.fail(
                    "Command 'MCP_Bridge_Toggle' is still listed under "
                    f"Customize category 'init_gui' (found: {init_gui_cmds})"
                )
        idx = combo.findData("MCP_Bridge")
        if idx < 0 or combo.itemText(idx) != "MCP_Bridge":
            common.fail(
                "Customize category 'MCP_Bridge' not found "
                f"(categories: {categories})"
            )

        commands = _commands_for_category(idx)
        if ("MCP_Bridge_Toggle", "MCP Bridge On/Off") not in commands:
            common.fail(
                "Command 'MCP_Bridge_Toggle' not listed under Customize "
                f"category 'MCP_Bridge' (found: {commands})"
            )
    finally:
        dlg.close()
        QCoreApplication.processEvents()

    common.log_notice(
        "Command 'MCP_Bridge_Toggle' is under category 'MCP_Bridge'"
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


_PREFS_LINE = (
    "To start or stop the bridge, click <b>MCP On/Off</b> "
    "in the status bar. While active, it listens "
    "on the addresses below."
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
    from freecad.mcp_bridge import config, file_prompt

    unrestricted = config.unrestricted()
    shown = button.icon().pixmap(16, 16).toImage()
    if button.icon().pixmap(16, 16).isNull() or shown != (
        file_prompt.mode_icon(unrestricted).pixmap(16, 16).toImage()
    ):
        common.fail(
            "Status-bar button icon is not the permission mode's "
            f"(unrestricted={unrestricted})"
        )
    if button.popupMode() != QToolButton.MenuButtonPopup:
        common.fail(
            "Status-bar button is not in MenuButtonPopup mode "
            f"(popupMode={button.popupMode()!r})"
        )
    menu = button.menu()
    texts = None if menu is None else [a.text() for a in menu.actions()]
    expected_menu = [
        "Permissions: Ask",
        "Permissions: Unrestricted",
        "MCP Bridge Preferences",
    ]
    if texts != expected_menu:
        common.fail(
            "Status-bar button menu is missing or unexpected: "
            f"expected {expected_menu!r}, got {texts!r}"
        )
    # Each item is plain words with its icon. None carries a tick: the
    # button's own icon is what shows the mode in force.
    for item in menu.actions():
        if item.isCheckable():
            common.fail(
                f"Status-bar button menu item {item.text()!r} is checkable"
            )
        if item.icon().pixmap(16, 16).isNull():
            common.fail(
                f"Status-bar button menu item {item.text()!r} has no icon"
            )
        if not item.isIconVisibleInMenu():
            common.fail(
                f"Status-bar button menu item {item.text()!r} hides "
                "its icon"
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
    action = common.find_toolbar_action()
    pressed = None if action is None else action.isChecked()
    if pressed != running:
        common.fail(
            "Toolbar button is out of step with the status-bar button "
            f"(pressed={pressed}, bridge running={running})"
        )
    action_tip = None if action is None else action.toolTip()
    if action_tip != tip:
        common.fail(
            "Toolbar button tooltip is out of step. "
            f"Expected {tip!r}, got {action_tip!r}"
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
        if common.find_toolbar_action() is None:
            common.fail(
                "Toolbar action disappeared when status-bar button was hidden"
            )
    finally:
        params.RemBool("ShowStatusButton")
    _assert_status_button(False)
    common.log_notice(
        "Status-bar button moves, hides and returns on preference change"
    )


def _answer_unrestricted_dialog(label: str) -> None:
    """Click `label` on the Unrestricted confirmation when it shows."""
    from PySide.QtCore import QTimer
    from PySide.QtWidgets import QApplication, QMessageBox

    attempts = {"left": 100}

    def tick():
        attempts["left"] -= 1
        app = QApplication.instance()
        widget = app.activeModalWidget() if app is not None else None
        if (
            isinstance(widget, QMessageBox)
            and widget.objectName() == "MCPBridgeUnrestrictedDialog"
        ):
            for btn in widget.buttons():
                if btn.text() == label:
                    btn.click()
                    return
        if attempts["left"] > 0:
            QTimer.singleShot(50, tick)

    QTimer.singleShot(50, tick)


def _verify_mode_on_status_button() -> None:
    """The On/Off button carries the permission mode: its menu switches
    it, its width keeps, and Unrestricted keeps the button in sight."""
    import FreeCAD as App

    from freecad.mcp_bridge import config
    from freecad.mcp_bridge.constants import PREF_GROUP

    params = App.ParamGet(PREF_GROUP)
    if config.unrestricted():
        common.fail("Permission mode is not Ask at the start of verify")
    button = _status_button()
    ask, unrestricted = button.menu().actions()[:2]
    width = button.sizeHint().width()
    try:
        # Unrestricted asks first, and No keeps Ask.
        _answer_unrestricted_dialog("No")
        unrestricted.trigger()
        if config.unrestricted():
            common.fail("No on the confirmation switched to Unrestricted")
        _assert_status_button(False)

        _answer_unrestricted_dialog("Yes")
        unrestricted.trigger()
        if not config.unrestricted():
            common.fail("Yes on the confirmation did not switch the mode")
        _assert_status_button(False)
        if _status_button().sizeHint().width() != width:
            common.fail(
                "Status-bar button width changed with the permission mode "
                f"({width} in Ask, {_status_button().sizeHint().width()} "
                "in Unrestricted)"
            )

        # Unrestricted stays shown on the status bar.
        params.SetBool("ShowStatusButton", False)
        try:
            if _status_button() is None:
                common.fail(
                    "Status-bar button was hidden while Unrestricted"
                )
            # Ask is at once, and the preference applies again.
            _status_button().menu().actions()[0].trigger()
            if config.unrestricted():
                common.fail("Choosing Ask did not switch the mode at once")
            if _status_button() is not None:
                common.fail(
                    "Status-bar button remained present in Ask with "
                    "ShowStatusButton=False"
                )
        finally:
            params.RemBool("ShowStatusButton")
    finally:
        config.set_unrestricted(False)
    _assert_status_button(False)
    common.log_notice(
        "Status-bar button shows the permission mode, and its menu "
        "switches it"
    )


def _verify_toolbar_hide() -> None:
    """Hiding and showing the toolbar persists across workbench switches."""
    import FreeCAD as App
    import FreeCADGui as Gui
    from PySide.QtCore import QCoreApplication
    from PySide.QtWidgets import QToolBar

    from freecad.mcp_bridge.constants import (
        DISPLAY_NAME,
        TOOLBAR_OBJECT_NAME,
    )

    mw = Gui.getMainWindow()
    if mw is None:
        common.fail("MainWindow is None during toolbar-hide check")
    tb_params = App.ParamGet("User parameter:BaseApp/MainWindow/Toolbars")
    initial_wb = (
        Gui.activeWorkbench().name()
        if Gui.activeWorkbench() is not None
        else "PartDesignWorkbench"
    )
    other_wb = (
        "PartWorkbench"
        if initial_wb != "PartWorkbench"
        else "PartDesignWorkbench"
    )

    def _find_toolbar():
        for tb in mw.findChildren(QToolBar):
            if (
                tb.windowTitle() == DISPLAY_NAME
                or tb.objectName() == TOOLBAR_OBJECT_NAME
            ):
                return tb
        return None

    def _wait_until(ready) -> bool:
        """Pump events until ready() is true, for up to 3 s."""
        deadline = time.monotonic() + 3.0
        while True:
            QCoreApplication.processEvents()
            if ready():
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)

    try:
        tb = _find_toolbar()
        if tb is None or not tb.isVisible():
            common.fail("MCP Bridge toolbar is missing or hidden at startup")
        toggle = tb.toggleViewAction()
        if toggle is None or not toggle.isVisible():
            common.fail(
                "MCP Bridge toolbar has no visible toggleViewAction"
            )

        toggle.trigger()
        if not _wait_until(lambda: not tb.isVisible()):
            common.fail(
                "MCP Bridge toolbar remained visible after toggleViewAction"
            )
        if _status_button() is None or not _status_button().isVisible():
            common.fail(
                "Status-bar button was not available while toolbar was hidden"
            )

        Gui.activateWorkbench(other_wb)

        def _still_hidden() -> bool:
            found = _find_toolbar()
            return found is not None and not found.isVisible()

        if not _wait_until(_still_hidden):
            tb = _find_toolbar()
            visible = None if tb is None else tb.isVisible()
            common.fail(
                "MCP Bridge toolbar did not stay hidden after workbench "
                f"switch (tb={tb!r}, visible={visible})"
            )
        tb = _find_toolbar()
        if tb_params.GetBool(DISPLAY_NAME, True):
            common.fail(
                "MainWindow/Toolbars parameter was not saved as False "
                "after workbench switch"
            )

        tb.toggleViewAction().trigger()
        if not _wait_until(lambda: tb.isVisible()):
            common.fail(
                "MCP Bridge toolbar did not become visible when shown again"
            )

        Gui.activateWorkbench(initial_wb)

        def _still_shown() -> bool:
            found = _find_toolbar()
            return found is not None and found.isVisible()

        if not _wait_until(_still_shown):
            tb = _find_toolbar()
            visible = None if tb is None else tb.isVisible()
            common.fail(
                "MCP Bridge toolbar did not stay shown after workbench "
                f"switch (tb={tb!r}, visible={visible})"
            )
        if not tb_params.GetBool(DISPLAY_NAME, False):
            common.fail(
                "MainWindow/Toolbars parameter was not saved as True "
                "after workbench switch"
            )
    finally:
        tb_params.SetBool(DISPLAY_NAME, True)
        if (
            Gui.activeWorkbench() is not None
            and Gui.activeWorkbench().name() != initial_wb
        ):
            Gui.activateWorkbench(initial_wb)
            QCoreApplication.processEvents()
        tb = _find_toolbar()
        if tb is not None and not tb.isVisible():
            tb.toggleViewAction().trigger()
            QCoreApplication.processEvents()

    _assert_status_button(False)
    common.log_notice(
        "MCP Bridge toolbar stays hidden and shown across workbench switches"
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

    from freecad.mcp_bridge import config, token_file
    from freecad.mcp_bridge.constants import PROTOCOL_VERSION

    entry = config.listen_addresses()[0]
    url = f"http://{entry}/mcp"
    timeout_s = timeout_ms / 1000.0
    result: dict = {}
    token = token_file.read_token()

    def _post(payload):
        # A valid request in the bridge's protocol version: version, client
        # info and capabilities in _meta, mirrored in the headers.
        # The token is the one FreeCAD wrote, from FREECAD_MCP_TOKEN_DIR
        # or <home>/.freecad-mcp-bridge.
        params = payload["params"]
        params["_meta"] = {
            "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
            "io.modelcontextprotocol/clientInfo": {
                "name": "test-verify",
                "version": "0",
            },
            "io.modelcontextprotocol/clientCapabilities": {},
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
            "Mcp-Method": payload["method"],
            "Mcp-Name": params["name"],
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
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
    from freecad.mcp_bridge import config

    # The probe is execute_python. In Unrestricted mode it meets no
    # dialog. The mode goes back to Ask, where the probe found Ask,
    # before any check below can end the process.
    was_unrestricted = config.unrestricted()
    config.set_unrestricted(True)
    probe = common.env("RELEASE_INSTALL_PROBE", 'print("test_verify_ok")')
    expected = common.env("RELEASE_INSTALL_PROBE_EXPECT", "test_verify_ok")
    timeout_ms = int(common.env("RELEASE_INSTALL_HTTP_TIMEOUT_MS", "30000"))

    try:
        result = _http_exec_probe(probe, timeout_ms)
    finally:
        if not was_unrestricted:
            config.set_unrestricted(False)

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
    _verify_command_category()
    _verify_preferences_line()
    common.check_bridge_off_at_startup()
    _assert_status_button(False)
    _verify_status_button_hide()
    _verify_mode_on_status_button()
    _verify_toolbar_hide()
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
