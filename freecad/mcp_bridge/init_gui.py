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
from freecad.mcp_bridge.resources import icon_path

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
                "Start or stop the MCP Bridge listener in this FreeCAD session"
            ),
        }

    def Activated(self):
        import FreeCAD as App
        import FreeCADGui as Gui

        try:
            inst = bridge._bridge_instance
            if not inst:
                import __main__

                inst = getattr(__main__, "_freecad_bridge_instance", None)
                if not inst:
                    inst = getattr(__main__, "_bridge_instance", None)

            if inst and inst.isListening():
                inst.close()
                App.Console.PrintMessage(f"{LOG_PREFIX} Stopped socket listener.\n")
                mw = Gui.getMainWindow()
                if mw:
                    mw.statusBar().showMessage(f"{DISPLAY_NAME}: Offline")
            else:
                if inst:
                    inst.close()
                bridge._bridge_instance = bridge.FreeCADBridge()
                App.Console.PrintMessage(f"{LOG_PREFIX} Started socket listener.\n")
                mw = Gui.getMainWindow()
                if mw:
                    mw.statusBar().showMessage(f"{DISPLAY_NAME}: Listening...")
        except Exception as e:
            App.Console.PrintError(f"{LOG_PREFIX} Error toggling bridge: {e}\n")


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
            "Start or stop the MCP Bridge listener in this FreeCAD session"
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

if not getattr(App, "MCPBridgeManipulatorAdded", False):
    App.MCPBridgeManipulatorAdded = True
    Gui.addWorkbenchManipulator(MCPBridgeUiManipulator())

schedule_ui_injection()