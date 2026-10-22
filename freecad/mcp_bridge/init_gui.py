import os

import FreeCAD as App
import FreeCADGui as Gui

from freecad.mcp_bridge import bridge
from freecad.mcp_bridge.resources import _mod_root
from freecad.mcp_bridge.constants import (
    COMMAND_ID,
    DISPLAY_NAME,
    LOG_PREFIX,
    STATUS_BUTTON_OBJECT_NAME,
    TOOLBAR_OBJECT_NAME,
)
from freecad.mcp_bridge.resources import icon_path, preferences_ui_path

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
        mw = Gui.getMainWindow()
        if mw:
            mw.statusBar().showMessage(status)
        show_toggle_state()
        if started or stopped:
            App.Console.PrintNotification(status + "\n")
        else:
            App.Console.PrintWarning(status + "\n")


def _refresh_status_button(running, tip):
    """Set the status-bar button's text, tooltip and emphasis."""
    import FreeCADGui as Gui
    from PySide.QtWidgets import QToolButton

    mw = Gui.getMainWindow()
    if mw is None:
        return
    button = mw.statusBar().findChild(
        QToolButton, STATUS_BUTTON_OBJECT_NAME
    )
    if button is None:
        return
    button.setText("MCP: On" if running else "MCP: Off")
    button.setToolTip(tip)
    if running:
        button.setStyleSheet(
            "QToolButton { color: palette(highlight); }"
        )
    else:
        button.setStyleSheet(
            "QToolButton { color: palette(window-text); }"
        )


def _ensure_status_button(mw):
    """Add the permanent status-bar button once; return it."""
    import FreeCAD as App
    import FreeCADGui as Gui
    from PySide.QtCore import QSize, Qt
    from PySide.QtGui import QIcon
    from PySide.QtWidgets import QToolButton

    bar = mw.statusBar()
    button = bar.findChild(QToolButton, STATUS_BUTTON_OBJECT_NAME)
    if button is not None:
        return button
    button = QToolButton(bar)
    button.setObjectName(STATUS_BUTTON_OBJECT_NAME)
    button.setAutoRaise(True)
    button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
    button.setIcon(QIcon(App.MCPBridgeIconPath))
    button.setIconSize(QSize(16, 16))
    command = App.MCPBridgeCommand
    button.clicked.connect(lambda *_: Gui.runCommand(command))
    bar.addPermanentWidget(button)
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