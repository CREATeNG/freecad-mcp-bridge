import os

import FreeCAD as App
import FreeCADGui as Gui

from freecad.mcp_bridge import bridge
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
        port_taken = False
        try:
            if bridge.is_running():
                bridge.stop()
                status = f"{DISPLAY_NAME}: Offline"
                stopped = True
            else:
                bridge.start()
                status = f"{DISPLAY_NAME}: Listening on 127.0.0.1:{config.port()}"
                started = True
        except Exception as e:
            App.Console.PrintDeveloperError(
                f"{LOG_PREFIX} Error toggling bridge: {e}\n"
            )
            status = f"{DISPLAY_NAME}: Could not start ({e})"
            port_taken = isinstance(e, OSError)
        mw = Gui.getMainWindow()
        if mw:
            mw.statusBar().showMessage(status)
        show_toggle_state()
        if started or stopped:
            App.Console.PrintNotification(status + "\n")
        else:
            warning = status
            if port_taken:
                warning += (
                    ". Change the port in Edit → "
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
    else:
        color = "palette(window-text)"
    button.setStyleSheet(
        f"QToolButton {{ color: {color}; padding-right: 15px; }} "
        "QToolButton:!hover { background-color: transparent; "
        "border-color: transparent; }"
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

    running = bridge.is_running()
    tip = (
        f"{DISPLAY_NAME}: on (click to stop)"
        if running
        else f"{DISPLAY_NAME}: off (click to start)"
    )
    action = getattr(App, "MCPBridgeAction", None)
    if action is not None:
        action.setChecked(running)
        action.setToolTip(tip)
    _refresh_status_button(running, tip)


def inject_ui():
    import FreeCAD as App
    import FreeCADGui as Gui
    from PySide.QtCore import QTimer
    from PySide.QtGui import QIcon
    from PySide.QtWidgets import QToolBar

    icon = App.MCPBridgeIconPath
    command = App.MCPBridgeCommand
    action_label = "MCP Bridge On/Off"

    mw = Gui.getMainWindow()
    if not mw:
        QTimer.singleShot(500, App.MCPBridgeInjectUi)
        return False

    target_tb = None
    for tb in mw.findChildren(QToolBar):
        if tb.windowTitle() == DISPLAY_NAME or tb.objectName() == TOOLBAR_OBJECT_NAME:
            target_tb = tb
            break

    if not target_tb:
        target_tb = QToolBar(DISPLAY_NAME, mw)
        target_tb.setObjectName(TOOLBAR_OBJECT_NAME)
        mw.addToolBar(target_tb)

    toolbar_exists = False
    for action in target_tb.actions():
        if action.text() == action_label:
            toolbar_exists = True
            break
    if not toolbar_exists:
        action = target_tb.addAction(action_label)
        action.setIcon(QIcon(icon))
        action.setCheckable(True)
        action.triggered.connect(lambda *_: Gui.runCommand(command))
        App.MCPBridgeAction = action

    _ensure_status_button(mw)
    show_toggle_state()

    target_tb.setVisible(True)
    target_tb.show()
    mw.update()

    ready = target_tb.isVisible() and mw.isVisible()
    if ready and not getattr(App, "MCPBridgeUiReady", False):
        App.MCPBridgeUiReady = True
        App.Console.PrintMessage(f"{LOG_PREFIX} Toolbar ready.\n")
    return ready


class MCPBridgeUiManipulator:
    """Re-inject the toolbar after each workbench activation."""

    def modifyToolBars(self):
        import FreeCAD as App

        if hasattr(App, "MCPBridgeInjectUi"):
            App.MCPBridgeInjectUi()
        return {}


class _StatusBarPrefObserver:
    """Refresh the status-bar button when its preferences are saved."""

    def onChange(self, _grp, param=None):
        if param in (None, "ShowStatusButton", "StatusButtonPosition"):
            show_toggle_state()


def ensure_startup_ui():
    """Wait for the main window, then inject and hook workbench changes."""
    import FreeCAD as App
    import FreeCADGui as Gui
    from PySide.QtCore import QTimer

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
        App.MCPBridgePrefGroup = App.ParamGet(PREF_GROUP)
        App.MCPBridgePrefObserver = _StatusBarPrefObserver()
        App.MCPBridgePrefGroup.Attach(App.MCPBridgePrefObserver)

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
Gui.addCommand(App.MCPBridgeCommand, MCPBridgeCommand())

if not getattr(App, "MCPBridgePrefAdded", False):
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