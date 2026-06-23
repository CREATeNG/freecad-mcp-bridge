import FreeCAD as App
import FreeCADGui as Gui

from freecad.mcp_bridge import bridge
from freecad.mcp_bridge.resources import icon_path

App.FreeCADMCPBridgeIconPath = icon_path()
App.FreeCADMCPBridgeCommand = "FreeCAD_MCP_Bridge_Toggle"


class AIAgentBridgeCommand:
    def GetResources(self):
        import FreeCAD as App

        return {
            "Pixmap": App.FreeCADMCPBridgeIconPath,
            "MenuText": "Start/Stop AI Agent Bridge",
            "ToolTip": "Toggle the local Named Pipe / UNIX socket bridge for AI agent control",
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
                App.Console.PrintMessage("[AI Bridge] Stopped socket listener.\n")
                mw = Gui.getMainWindow()
                if mw:
                    mw.statusBar().showMessage("AI Agent Bridge: Offline")
            else:
                if inst:
                    inst.close()
                bridge._bridge_instance = bridge.FreeCADBridge()
                App.Console.PrintMessage("[AI Bridge] Started socket listener.\n")
                mw = Gui.getMainWindow()
                if mw:
                    mw.statusBar().showMessage("AI Agent Bridge: Listening...")
        except Exception as e:
            App.Console.PrintError(f"[AI Bridge] Error toggling bridge: {e}\n")


def inject_ui():
    import FreeCAD as App
    import FreeCADGui as Gui
    from PySide.QtCore import QTimer
    from PySide.QtGui import QIcon
    from PySide.QtWidgets import QToolBar

    icon = App.FreeCADMCPBridgeIconPath
    command = App.FreeCADMCPBridgeCommand
    action_label = "Start/Stop AI Agent Bridge"

    mw = Gui.getMainWindow()
    if not mw:
        QTimer.singleShot(500, App.FreeCADMCPBridgeInjectUi)
        return False

    target_tb = None
    for tb in mw.findChildren(QToolBar):
        if tb.windowTitle() == "AI Bridge" or tb.objectName() == "AI_Bridge":
            target_tb = tb
            break

    if not target_tb:
        target_tb = QToolBar("AI Bridge", mw)
        target_tb.setObjectName("AI_Bridge")
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
            "Toggle the local Named Pipe / UNIX socket bridge for AI agent control"
        )
        action.triggered.connect(lambda: Gui.runCommand(command))

    target_tb.setVisible(True)
    target_tb.show()
    mw.update()

    ready = target_tb.isVisible() and mw.isVisible()
    if ready and not getattr(App, "FreeCADMCPBridgeUiReady", False):
        App.FreeCADMCPBridgeUiReady = True
        App.Console.PrintMessage("[AI Bridge] Toolbar ready.\n")
    return ready


class MCPBridgeUiManipulator:
    """Re-inject the toolbar after each workbench activation."""

    def modifyToolBars(self):
        import FreeCAD as App

        if hasattr(App, "FreeCADMCPBridgeInjectUi"):
            App.FreeCADMCPBridgeInjectUi()
        return {}


def ensure_startup_ui():
    """Wait for the main window, then inject and hook workbench changes."""
    import FreeCAD as App
    import FreeCADGui as Gui
    from PySide.QtCore import QTimer

    mw = Gui.getMainWindow()
    if mw is None:
        QTimer.singleShot(250, App.FreeCADMCPBridgeEnsureStartupUi)
        return

    try:
        mw.workbenchActivated
    except AttributeError:
        QTimer.singleShot(250, App.FreeCADMCPBridgeEnsureStartupUi)
        return

    if not getattr(App, "FreeCADMCPBridgeHooksConnected", False):
        App.FreeCADMCPBridgeHooksConnected = True
        mw.workbenchActivated.connect(lambda *_args: App.FreeCADMCPBridgeInjectUi())

    App.FreeCADMCPBridgeInjectUi()


def schedule_ui_injection():
    import FreeCAD as App
    from PySide.QtCore import QTimer

    QTimer.singleShot(0, App.FreeCADMCPBridgeEnsureStartupUi)
    for delay in (1000, 2500, 5000):
        QTimer.singleShot(delay, App.FreeCADMCPBridgeInjectUi)


App.FreeCADMCPBridgeInjectUi = inject_ui
App.FreeCADMCPBridgeEnsureStartupUi = ensure_startup_ui

if App.FreeCADMCPBridgeCommand in Gui.listCommands():
    try:
        Gui.removeCommand(App.FreeCADMCPBridgeCommand)
    except Exception:
        pass
Gui.addCommand(App.FreeCADMCPBridgeCommand, AIAgentBridgeCommand())

if not getattr(App, "FreeCADMCPBridgeManipulatorAdded", False):
    App.FreeCADMCPBridgeManipulatorAdded = True
    Gui.addWorkbenchManipulator(MCPBridgeUiManipulator())

schedule_ui_injection()