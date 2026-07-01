import os

import FreeCAD as App
import FreeCADGui as Gui

from freecad.mcp_bridge import bridge
from freecad.mcp_bridge.resources import _mod_root
from freecad.mcp_bridge.constants import (
    COMMAND_ID,
    DISPLAY_NAME,
    LOG_PREFIX,
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

        try:
            if bridge.is_running():
                bridge.stop()
                status = f"{DISPLAY_NAME}: Offline"
            else:
                bridge.start()
                status = f"{DISPLAY_NAME}: Listening on 127.0.0.1:{config.port()}"
            mw = Gui.getMainWindow()
            if mw:
                mw.statusBar().showMessage(status)
        except Exception as e:
            App.Console.PrintError(f"{LOG_PREFIX} Error toggling bridge: {e}\n")
            mw = Gui.getMainWindow()
            if mw:
                mw.statusBar().showMessage(f"{DISPLAY_NAME}: Offline")


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
        action.setToolTip(
            "Start or stop the MCP Bridge server in this FreeCAD session"
        )
        action.triggered.connect(lambda: Gui.runCommand(command))

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