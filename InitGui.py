import os
import sys

import FreeCAD as App
import FreeCADGui as Gui

# FreeCAD loads this file with exec() inside a function. Only imported modules and
# App attributes are visible to class bodies and deferred callbacks.
_mod_dir = None
try:
    _mod_dir = os.path.dirname(os.path.abspath(__file__))
except NameError:
    for mod_root in App.ConfigGet("ModDirs"):
        if not os.path.isdir(mod_root):
            continue
        for entry in os.listdir(mod_root):
            candidate = os.path.join(mod_root, entry)
            if os.path.isfile(os.path.join(candidate, "freecad_bridge.py")):
                _mod_dir = candidate
                break
        if _mod_dir:
            break
if not _mod_dir:
    _mod_dir = os.getcwd()

if _mod_dir not in sys.path:
    sys.path.insert(0, _mod_dir)

App.FreeCADMCPBridgeModDir = _mod_dir
App.FreeCADMCPBridgeIconPath = os.path.join(_mod_dir, "icon.svg").replace("\\", "/")
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
            mod_dir = App.FreeCADMCPBridgeModDir
            if mod_dir not in sys.path:
                sys.path.insert(0, mod_dir)
            import freecad_bridge

            inst = getattr(freecad_bridge, "_freecad_bridge_instance", None)
            if not inst:
                import __main__
                inst = getattr(__main__, "_freecad_bridge_instance", None)

            if inst and inst.isListening():
                inst.close()
                App.Console.PrintMessage("[AI Bridge] Stopped socket listener.\n")
                mw = Gui.getMainWindow()
                if mw:
                    mw.statusBar().showMessage("AI Agent Bridge: Offline")
            else:
                if inst:
                    inst.close()
                freecad_bridge._freecad_bridge_instance = freecad_bridge.FreeCADBridge()
                App.Console.PrintMessage("[AI Bridge] Started socket listener.\n")
                mw = Gui.getMainWindow()
                if mw:
                    mw.statusBar().showMessage("AI Agent Bridge: Listening...")
        except Exception as e:
            App.Console.PrintError(f"[AI Bridge] Error toggling bridge: {e}\n")


def inject_ui():
    import FreeCAD as App
    import FreeCADGui as Gui

    QtCore = None
    QtGui = None
    QtWidgets = None
    for core_name, gui_name, widgets_name in (
        ("PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets"),
        ("PySide2.QtCore", "PySide2.QtGui", "PySide2.QtWidgets"),
        ("PySide.QtCore", "PySide.QtGui", "PySide.QtWidgets"),
    ):
        try:
            QtCore = __import__(core_name, fromlist=["QtCore"])
            QtGui = __import__(gui_name, fromlist=["QtGui"])
            QtWidgets = __import__(widgets_name, fromlist=["QtWidgets"])
            break
        except ImportError:
            continue
    if QtCore is None:
        App.Console.PrintError("[AI Bridge] UI injection skipped: no Qt binding found\n")
        return False

    QTimer = QtCore.QTimer
    QMenu = QtWidgets.QMenu
    QToolBar = QtWidgets.QToolBar
    QIcon = QtGui.QIcon
    icon_path = App.FreeCADMCPBridgeIconPath
    command = App.FreeCADMCPBridgeCommand

    mw = Gui.getMainWindow()
    if not mw:
        QTimer.singleShot(500, App.FreeCADMCPBridgeInjectUi)
        return False

    tools_menu = None
    for menu in mw.menuBar().findChildren(QMenu):
        title = menu.title().replace("&", "").strip().lower()
        if title == "tools" or menu.objectName().lower() == "tools":
            tools_menu = menu
            break

    if tools_menu:
        exists = False
        for action in tools_menu.actions():
            if action.text() == "Start/Stop AI Agent Bridge":
                exists = True
                break
        if not exists:
            tools_menu.addSeparator()
            action = tools_menu.addAction("Start/Stop AI Agent Bridge")
            action.triggered.connect(lambda: Gui.runCommand(command))

    target_tb = None
    for tb in mw.findChildren(QToolBar):
        if tb.windowTitle() == "AI Bridge" or tb.objectName() == "AI_Bridge":
            target_tb = tb
            break

    if not target_tb:
        target_tb = QToolBar("AI Bridge", mw)
        target_tb.setObjectName("AI_Bridge")
        mw.addToolBar(target_tb)

    exists = False
    for action in target_tb.actions():
        if action.text() == "Start/Stop AI Agent Bridge":
            exists = True
            break
    if not exists:
        action = target_tb.addAction("Start/Stop AI Agent Bridge")
        action.setIcon(QIcon(icon_path))
        action.setToolTip("Toggle the local Named Pipe / UNIX socket bridge for AI agent control")
        action.triggered.connect(lambda: Gui.runCommand(command))

    has_button = False
    for action in target_tb.actions():
        if action.text() == "Start/Stop AI Agent Bridge":
            has_button = True
            break

    target_tb.setVisible(True)
    target_tb.show()
    mw.update()

    ready = has_button and target_tb.isVisible() and mw.isVisible()
    if ready and not getattr(App, "FreeCADMCPBridgeUiReady", False):
        App.FreeCADMCPBridgeUiReady = True
        App.Console.PrintMessage("[AI Bridge] Toolbar and menu ready.\n")
    return ready


class MCPBridgeUiManipulator:
    """Re-inject after each workbench activation once FreeCAD has built the UI."""

    def modifyMenuBar(self):
        import FreeCAD as App
        if hasattr(App, "FreeCADMCPBridgeInjectUi"):
            App.FreeCADMCPBridgeInjectUi()
        return {}

    def modifyToolBars(self):
        import FreeCAD as App
        if hasattr(App, "FreeCADMCPBridgeInjectUi"):
            App.FreeCADMCPBridgeInjectUi()
        return {}


def schedule_ui_injection():
    import FreeCAD as App

    QtCore = None
    for core_name in ("PySide6.QtCore", "PySide2.QtCore", "PySide.QtCore"):
        try:
            QtCore = __import__(core_name, fromlist=["QtCore"])
            break
        except ImportError:
            continue
    if QtCore is None:
        App.Console.PrintError("[AI Bridge] Could not schedule UI injection: no Qt binding found\n")
        return
    for delay in (1000, 2500, 5000):
        QtCore.QTimer.singleShot(delay, App.FreeCADMCPBridgeInjectUi)


# Keep a persistent callback for QTimer under exec() loading.
App.FreeCADMCPBridgeInjectUi = inject_ui

if App.FreeCADMCPBridgeCommand in Gui.listCommands():
    try:
        Gui.removeCommand(App.FreeCADMCPBridgeCommand)
    except Exception:
        pass
Gui.addCommand(App.FreeCADMCPBridgeCommand, AIAgentBridgeCommand())

if not getattr(App, "FreeCADMCPBridgeManipulatorAdded", False):
    App.FreeCADMCPBridgeManipulatorAdded = True
    Gui.addWorkbenchManipulator(MCPBridgeUiManipulator())

# Do not inject synchronously here — FreeCAD rebuilds menus/toolbars after InitGui runs.
schedule_ui_injection()