import os

import FreeCAD as App
import FreeCADGui as Gui

from freecad.mcp_bridge import MCP_Bridge, bridge
from freecad.mcp_bridge.constants import (
    COMMAND_ID,
    DISPLAY_NAME,
    LOG_PREFIX,
    PREF_GROUP,
    STATUS_BUTTON_LEFT,
    STATUS_BUTTON_OBJECT_NAME,
    TOOLBAR_OBJECT_NAME,
)
from freecad.mcp_bridge.resources import (
    _mod_root,
    icon_path,
    preferences_ui_path,
)

App.MCPBridgeIconPath = icon_path()
App.MCPBridgeCommand = COMMAND_ID

_icons_dir = os.path.join(_mod_root(), "Resources", "Icons")
if os.path.isdir(_icons_dir):
    Gui.addIconPath(_icons_dir.replace("\\", "/"))


def _risk_dialog_text(entries, require_token=True, require_tls=True):
    """Return (title, text) for the first-time non-loopback risk dialog."""
    if isinstance(entries, str):
        entries = [entries]
    unaccepted_endpoints = "\n".join(entries)
    title = f"{DISPLAY_NAME} — Listen beyond this machine?"
    bullets = []
    if not require_token:
        bullets.append(
            "• No authentication: any machine that can reach these "
            "addresses can call the bridge."
        )
    if not require_tls:
        if require_token:
            bullets.append(
                "• No encryption: requests, output and the bearer token "
                "travel over plain HTTP."
            )
        else:
            bullets.append(
                "• No encryption: requests and output travel over plain "
                "HTTP."
            )
    if require_token:
        bullets.append(
            "• Code execution: any client with the bearer token that can "
            "reach these addresses can run Python code in FreeCAD as your "
            "user account."
        )
    else:
        bullets.append(
            "• Code execution: any machine that can reach these addresses "
            "can run Python code in FreeCAD as your user account."
        )
    bullet_block = "\n".join(bullets)
    body = f"""Listening on these addresses makes this FreeCAD session \
reachable from other machines:
{unaccepted_endpoints}

{bullet_block}

Accept these risks and start the bridge?"""
    return title, body


def _confirm_non_loopback_risk(
    entries, require_token=True, require_tls=True
):
    """Ask the user to accept the risks of listening on `entries`."""
    import FreeCADGui as Gui
    from PySide.QtWidgets import QDialog, QMessageBox

    title, body = _risk_dialog_text(
        entries, require_token=require_token, require_tls=require_tls
    )
    box = QMessageBox(
        QMessageBox.Warning,
        title,
        body,
        QMessageBox.NoButton,
        Gui.getMainWindow(),
    )
    QDialog.setWindowTitle(box, title)
    box.setObjectName("MCPBridgeRiskDialog")
    accept_btn = box.addButton(
        "Accept Risks and Start", QMessageBox.AcceptRole
    )
    cancel_btn = box.addButton("Cancel", QMessageBox.RejectRole)
    box.setDefaultButton(cancel_btn)
    box.setEscapeButton(cancel_btn)
    try:
        box.exec()
        return box.clickedButton() is accept_btn
    finally:
        box.setParent(None)
        box.deleteLater()


def _token_dialog_text(token):
    """Return (title, text) for the one-time bearer token dialog."""
    title = f"{DISPLAY_NAME} — Bearer token"
    body = f"""Copy this bearer token now. It will not be stored or shown \
again. Only a one-way hash is kept in FreeCAD's preferences.

{token}

Send it from your MCP client in the Authorization header:
Authorization: Bearer {token}

If you lose it, generate a new one in Edit → Preferences → MCP Bridge. \
A new token replaces the old one."""
    return title, body


def _show_generated_token(token, parent=None):
    """Display `token` once in a modal dialog with a Copy Token button."""
    import FreeCADGui as Gui
    from PySide.QtCore import Qt
    from PySide.QtGui import QGuiApplication
    from PySide.QtWidgets import QDialog, QMessageBox

    title, body = _token_dialog_text(token)
    host = parent if parent is not None else Gui.getMainWindow()
    box = QMessageBox(
        QMessageBox.Information,
        title,
        body,
        QMessageBox.NoButton,
        host,
    )
    QDialog.setWindowTitle(box, title)
    box.setObjectName("MCPBridgeTokenDialog")
    box.setTextInteractionFlags(Qt.TextSelectableByMouse)
    copy_btn = box.addButton("Copy Token", QMessageBox.ActionRole)
    ok_btn = box.addButton("OK", QMessageBox.AcceptRole)
    box.setDefaultButton(ok_btn)
    box.setEscapeButton(ok_btn)
    try:
        while True:
            box.exec()
            if box.clickedButton() is copy_btn:
                clipboard = QGuiApplication.clipboard()
                if clipboard is not None:
                    clipboard.setText(token)
                continue
            break
    finally:
        box.setParent(None)
        box.deleteLater()


def generate_and_show_token(parent=None):
    """Generate a new bearer token, store its hash, and show it once."""
    from freecad.mcp_bridge import config

    token = config.generate_and_store_token()
    _show_generated_token(token, parent=parent)
    return token


def _non_loopback_status(endpoints, require_token, require_tls):
    """Return the status-bar line for a start with a non-loopback entry."""
    if require_token and require_tls:
        warning = "code execution from other machines"
    elif not require_token and not require_tls:
        warning = "no authentication or encryption"
    elif not require_token:
        warning = "no authentication"
    else:
        warning = "no encryption"
    return f"{DISPLAY_NAME}: Listening on {endpoints} (warning: {warning})"


class MCPBridgeCommand:
    def GetResources(self):
        import FreeCAD as App

        return {
            "Pixmap": App.MCPBridgeIconPath,
            "MenuText": "MCP Bridge On/Off",
            "ToolTip": (
                "Start or stop the MCP Bridge server in this FreeCAD session"
            ),
        }

    def Activated(self):
        import FreeCAD as App
        import FreeCADGui as Gui

        from freecad.mcp_bridge import config

        started = False
        stopped = False
        bad_listen = False
        try:
            if bridge.is_running():
                bridge.stop()
                status = f"{DISPLAY_NAME}: Offline"
                stopped = True
            else:
                entries = config.listen_addresses()
                req_token = config.require_token()
                req_tls = config.require_tls()
                req_on_loopback = config.require_on_loopback()
                cert_file = config.tls_cert_file()
                key_file = config.tls_key_file()
                for entry in entries:
                    config.parse_listen_entry(entry)
                non_loopback = [
                    entry
                    for entry in entries
                    if not config.is_loopback_address(entry)
                ]
                protected = entries if req_on_loopback else non_loopback
                if protected and req_tls:
                    config.build_ssl_context(cert_file, key_file)
                unaccepted = [
                    entry
                    for entry in non_loopback
                    if not config.is_address_accepted(entry)
                ]
                if unaccepted and not _confirm_non_loopback_risk(
                    unaccepted,
                    require_token=req_token,
                    require_tls=req_tls,
                ):
                    status = f"{DISPLAY_NAME}: Offline"
                    mw = Gui.getMainWindow()
                    if mw:
                        mw.statusBar().showMessage(status)
                    show_toggle_state()
                    App.Console.PrintNotification(status + "\n")
                    return
                if protected and req_token and not config.token_hash():
                    generate_and_show_token()
                bridge.start(
                    addresses=entries,
                    require_token=req_token,
                    require_tls=req_tls,
                    tls_cert_file=cert_file,
                    tls_key_file=key_file,
                    require_on_loopback=req_on_loopback,
                )
                for entry in unaccepted:
                    config.accept_address(entry)
                endpoints = ", ".join(entries)
                if non_loopback:
                    status = _non_loopback_status(
                        endpoints, req_token, req_tls
                    )
                else:
                    status = f"{DISPLAY_NAME}: Listening on {endpoints}"
                started = True
        except Exception as e:
            App.Console.PrintDeveloperError(
                f"{LOG_PREFIX} Error toggling bridge: {e}\n"
            )
            status = f"{DISPLAY_NAME}: Could not start ({e})"
            bad_listen = isinstance(
                e, (OSError, ValueError)
            ) and "Preferences → MCP Bridge" not in str(e)
        mw = Gui.getMainWindow()
        if mw:
            mw.statusBar().showMessage(status)
        show_toggle_state()
        if started or stopped:
            App.Console.PrintNotification(status + "\n")
        else:
            warning = status
            if bad_listen:
                warning += (
                    ". Change the listen address in Edit → "
                    "Preferences → MCP Bridge."
                )
            App.Console.PrintWarning(warning + "\n")


def _status_button_on_color(rgb):
    """Return the 'On' green hex for a status-bar background (r, g, b)."""
    lin = [
        (c / 255.0) / 12.92
        if c / 255.0 <= 0.04045
        else ((c / 255.0 + 0.055) / 1.055) ** 2.4
        for c in rgb
    ]
    lum = 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    return "#56d364" if lum < 0.2282 else "#116329"


def _refresh_status_button(running, tip):
    """Set the status-bar button's presence, position, text and colour."""
    import FreeCADGui as Gui
    from PySide.QtGui import QPalette

    mw = Gui.getMainWindow()
    if mw is None:
        return
    button = _ensure_status_button(mw)
    if button is None:
        return
    button.setText("MCP On" if running else "MCP Off")
    button.setToolTip(tip)
    if running:
        bg = mw.statusBar().palette().color(QPalette.Window)
        color = _status_button_on_color((bg.red(), bg.green(), bg.blue()))
        color_rule = f"color: {color}; "
    else:
        color_rule = ""
    button.setStyleSheet(
        f"""
        QToolButton {{ {color_rule}padding-right: 15px; }}
        QToolButton:!hover {{
            background-color: transparent;
            border-color: transparent;
        }}
        """
    )


def _status_bar_widgets(bar):
    """Return (normal, permanent) widget lists in left-to-right bar order.

    Returns (None, None) if the status bar's internal row layout does not
    match Qt's QStatusBar schema.
    """
    from PySide.QtCore import Qt
    from PySide.QtWidgets import QHBoxLayout, QLayout, QWidget

    if bar is None:
        return None, None
    candidates = []
    for layout in bar.findChildren(QHBoxLayout):
        if not isinstance(layout, QLayout):
            continue
        current = layout
        depth = 0
        while isinstance(current, QLayout):
            depth += 1
            current = current.parent()
        if current is bar:
            candidates.append((depth, layout))
    if not candidates:
        return None, None
    max_depth = max(depth for depth, _ in candidates)
    deepest = [
        layout for depth, layout in candidates if depth == max_depth
    ]
    if len(deepest) != 1:
        return None, None
    box = deepest[0]
    slots = {}
    for widget in bar.findChildren(
        QWidget, options=Qt.FindDirectChildrenOnly
    ):
        index = box.indexOf(widget)
        if index >= 0:
            if index in slots:
                return None, None
            slots[index] = widget
    separator = 1
    while separator in slots:
        separator += 1
    count = box.count()
    expected = set(range(1, separator)) | set(
        range(separator + 1, count - 1)
    )
    if separator >= count - 1 or set(slots) != expected:
        return None, None
    return (
        [slots[i] for i in range(1, separator)],
        [slots[i] for i in range(separator + 1, count - 1)],
    )


def _ensure_status_button(mw):
    """Place or remove the permanent status-bar button; return it."""
    import FreeCAD as App
    import FreeCADGui as Gui
    from PySide.QtCore import QEvent, QObject, Qt
    from PySide.QtWidgets import QMenu, QToolButton

    from freecad.mcp_bridge import config

    class _BarThemeFilter(QObject):
        _reported = False

        def eventFilter(self, _obj, event):
            if event.type() in (
                QEvent.PaletteChange,
                QEvent.StyleChange,
            ):
                try:
                    show_toggle_state()
                except Exception as exc:
                    if not self._reported:
                        self._reported = True
                        App.Console.PrintDeveloperError(
                            f"{LOG_PREFIX} Error refreshing status-bar "
                            f"button: {exc}\n"
                        )
            return False

    def _report_layout_unavailable():
        if not getattr(App, "MCPBridgeLayoutErrorReported", False):
            App.MCPBridgeLayoutErrorReported = True
            App.Console.PrintDeveloperError(
                f"{LOG_PREFIX} Status-bar layout unavailable; "
                "the On/Off button may not be first among the "
                "right-hand items.\n"
            )

    bar = mw.statusBar()
    button = bar.findChild(QToolButton, STATUS_BUTTON_OBJECT_NAME)
    if not config.show_status_button():
        if button is not None:
            bar.removeWidget(button)
            button.setParent(None)
            button.deleteLater()
        return None
    position = config.status_button_position()
    if button is not None:
        if position == STATUS_BUTTON_LEFT:
            _, permanent = _status_bar_widgets(bar)
            if permanent is None:
                _report_layout_unavailable()
            in_place = permanent is None or (
                bool(permanent) and permanent[0] is button
            )
        else:
            in_place = button.property("mcp_position") == position
        if in_place and button.property("mcp_position") == position:
            return button
        bar.removeWidget(button)
        button.setParent(None)
        button.deleteLater()
    button = QToolButton(bar)
    button.setObjectName(STATUS_BUTTON_OBJECT_NAME)
    button.setProperty("mcp_position", position)
    button.setAutoRaise(True)
    button.setToolButtonStyle(Qt.ToolButtonTextOnly)
    button.setPopupMode(QToolButton.MenuButtonPopup)
    menu = QMenu(button)
    prefs_action = menu.addAction("Preferences")
    prefs_action.triggered.connect(
        lambda *_: Gui.showPreferences(DISPLAY_NAME, 0)
    )
    button.setMenu(menu)
    command = App.MCPBridgeCommand
    button.clicked.connect(lambda *_: Gui.runCommand(command))
    theme_filter = _BarThemeFilter(button)
    button._theme_filter = theme_filter
    bar.installEventFilter(theme_filter)
    if position == STATUS_BUTTON_LEFT:
        normal, _ = _status_bar_widgets(bar)
        if normal is None:
            _report_layout_unavailable()
            bar.addPermanentWidget(button)
        else:
            bar.insertPermanentWidget(len(normal), button)
    else:
        bar.addPermanentWidget(button)
    button.show()
    return button


def show_toggle_state():
    """Show whether the bridge is on, on the toolbar and the status bar.

    The toolbar button stays pressed while it runs. Both use the same
    tooltip.
    """
    import FreeCAD as App
    import FreeCADGui as Gui

    running = bridge.is_running()
    tip = (
        f"{DISPLAY_NAME}: on (click to stop)"
        if running
        else f"{DISPLAY_NAME}: off (click to start)"
    )
    cmd = Gui.Command.get(App.MCPBridgeCommand)
    for action in cmd.getAction() if cmd is not None else ():
        if not action.isCheckable():
            action.setCheckable(True)
        action.setChecked(running)
        action.setToolTip(tip)
    _refresh_status_button(running, tip)


def inject_ui():
    import FreeCAD as App
    import FreeCADGui as Gui
    from PySide.QtCore import QTimer
    from PySide.QtWidgets import QToolBar

    mw = Gui.getMainWindow()
    if not mw:
        QTimer.singleShot(500, App.MCPBridgeInjectUi)
        return False

    target_tb = None
    for tb in mw.findChildren(QToolBar):
        if (
            tb.windowTitle() == DISPLAY_NAME
            or tb.objectName() == TOOLBAR_OBJECT_NAME
        ):
            target_tb = tb
            break

    _ensure_status_button(mw)
    show_toggle_state()
    mw.update()

    ready = (
        target_tb is not None and target_tb.isVisible() and mw.isVisible()
    )
    if ready and not getattr(App, "MCPBridgeUiReady", False):
        App.MCPBridgeUiReady = True
        App.Console.PrintMessage(f"{LOG_PREFIX} Toolbar ready.\n")
    return ready


class MCPBridgeUiManipulator:
    """Add the MCP Bridge toolbar to each workbench."""

    def modifyToolBars(self):
        import FreeCAD as App
        import FreeCADGui as Gui
        from PySide.QtCore import QTimer

        wb = Gui.activeWorkbench()
        if wb is None or type(wb).__name__ == "NoneWorkbench":
            return []
        if hasattr(App, "MCPBridgeInjectUi"):
            QTimer.singleShot(0, App.MCPBridgeInjectUi)
        # modifyToolBars has no separate operation to create a toolbar:
        # each append entry adds a child to the ToolBarItem named by
        # toolBar. The first entry targets the root item's empty name ("")
        # to create the DISPLAY_NAME toolbar under the root, and the
        # second adds the command to that toolbar.
        return [
            {"append": DISPLAY_NAME, "toolBar": ""},
            {"append": App.MCPBridgeCommand, "toolBar": DISPLAY_NAME},
        ]


class _StatusBarPrefObserver:
    """Refresh the status-bar button and migrate a legacy Port on save."""

    def onChange(self, _grp, param=None):
        from freecad.mcp_bridge import config

        if param in (None, "Port", "ListenAddress"):
            config.migrate_legacy_port()
        if param in (None, "ShowStatusButton", "StatusButtonPosition"):
            show_toggle_state()


def _install_pref_page_filter(mw):
    """Connect `generateTokenButton` when the Preferences page opens."""
    try:
        from PySide.QtCore import QEvent, QObject
        from PySide.QtWidgets import QApplication, QPushButton
    except Exception:
        return

    app = QApplication.instance()
    if app is None or getattr(mw, "_mcp_pref_filter", None) is not None:
        return

    class _PrefPageFilter(QObject):
        def eventFilter(self, obj, event):
            if (
                event.type() == QEvent.Show
                and isinstance(obj, QPushButton)
                and obj.objectName() == "generateTokenButton"
                and not obj.property("mcp_token_hooked")
            ):
                obj.setProperty("mcp_token_hooked", True)
                obj.clicked.connect(
                    lambda *_: generate_and_show_token(parent=obj.window())
                )
            return False

    flt = _PrefPageFilter(mw)
    mw._mcp_pref_filter = flt
    app.installEventFilter(flt)


def ensure_startup_ui():
    """Wait for the main window, then inject and hook workbench changes."""
    import FreeCAD as App
    import FreeCADGui as Gui
    from PySide.QtCore import QTimer

    from freecad.mcp_bridge import config

    mw = Gui.getMainWindow()
    if mw is None:
        QTimer.singleShot(250, App.MCPBridgeEnsureStartupUi)
        return

    try:
        mw.workbenchActivated
    except AttributeError:
        QTimer.singleShot(250, App.MCPBridgeEnsureStartupUi)
        return

    if not getattr(App, "MCPBridgeHooksConnected", False):
        App.MCPBridgeHooksConnected = True
        mw.workbenchActivated.connect(lambda *_args: App.MCPBridgeInjectUi())
        config.migrate_legacy_port()
        App.MCPBridgePrefGroup = App.ParamGet(PREF_GROUP)
        App.MCPBridgePrefObserver = _StatusBarPrefObserver()
        App.MCPBridgePrefGroup.Attach(App.MCPBridgePrefObserver)
        _install_pref_page_filter(mw)

    App.MCPBridgeInjectUi()


def schedule_ui_injection():
    import FreeCAD as App
    from PySide.QtCore import QTimer

    QTimer.singleShot(0, App.MCPBridgeEnsureStartupUi)
    for delay in (1000, 2500, 5000):
        QTimer.singleShot(delay, App.MCPBridgeInjectUi)


App.MCPBridgeInjectUi = inject_ui
App.MCPBridgeEnsureStartupUi = ensure_startup_ui

if App.MCPBridgeCommand in Gui.listCommands():
    try:
        Gui.removeCommand(App.MCPBridgeCommand)
    except Exception:
        pass
MCP_Bridge.register(App.MCPBridgeCommand, MCPBridgeCommand())

if not getattr(App, "MCPBridgePrefAdded", False):
    from freecad.mcp_bridge import config

    try:
        config.migrate_legacy_port()
    except Exception:
        pass
    _prefs_ui = preferences_ui_path()
    if os.path.isfile(_prefs_ui):
        try:
            Gui.addPreferencePage(_prefs_ui, DISPLAY_NAME)
            App.MCPBridgePrefAdded = True
        except Exception as exc:
            App.Console.PrintWarning(
                f"{LOG_PREFIX} Could not add preference page: {exc}\n"
            )

if not getattr(App, "MCPBridgeManipulatorAdded", False):
    App.MCPBridgeManipulatorAdded = True
    Gui.addWorkbenchManipulator(MCPBridgeUiManipulator())

schedule_ui_injection()
