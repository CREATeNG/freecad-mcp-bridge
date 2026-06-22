import os
import sys

import FreeCAD as App
import FreeCADGui as Gui

_COMMAND = "FreeCAD_MCP_Bridge_Toggle"
_WORKBENCH = "FreeCAD_MCP_Bridge_Workbench"
_UI_SCHEDULED = "_freecad_mcp_bridge_ui_scheduled"


def _resolve_mod_dir():
    """Return the addon folder path.

    FreeCAD loads InitGui.py via exec(), so __file__ is not always defined.
    """
    try:
        return os.path.dirname(os.path.abspath(__file__))
    except NameError:
        for mod_root in App.ConfigGet("ModDirs"):
            if not os.path.isdir(mod_root):
                continue
            for entry in os.listdir(mod_root):
                candidate = os.path.join(mod_root, entry)
                if os.path.isfile(os.path.join(candidate, "freecad_bridge.py")):
                    return candidate
        return os.getcwd()


def _ensure_mod_path():
    mod_dir = _resolve_mod_dir()
    if mod_dir not in sys.path:
        sys.path.insert(0, mod_dir)
    return mod_dir


def _icon_path():
    return os.path.join(_ensure_mod_path(), "icon.svg").replace("\\", "/")


def _qt():
    """Return (QtCore, QtGui, QtWidgets) for the active FreeCAD Qt binding."""
    for core_name, gui_name, widgets_name in (
        ("PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets"),
        ("PySide2.QtCore", "PySide2.QtGui", "PySide2.QtWidgets"),
        ("PySide.QtCore", "PySide.QtGui", "PySide.QtWidgets"),
    ):
        try:
            QtCore = __import__(core_name, fromlist=["QtCore"])
            QtGui = __import__(gui_name, fromlist=["QtGui"])
            QtWidgets = __import__(widgets_name, fromlist=["QtWidgets"])
            return QtCore, QtGui, QtWidgets
        except ImportError:
            continue
    raise ImportError("No compatible Qt binding found for AI Agent Bridge")


class AIAgentBridgeCommand:
    def GetResources(self):
        return {
            "Pixmap": _icon_path(),
            "MenuText": "Start/Stop AI Agent Bridge",
            "ToolTip": "Toggle the local Named Pipe / UNIX socket bridge for AI agent control",
        }

    def Activated(self):
        try:
            _ensure_mod_path()
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


class FreeCAD_MCP_Bridge_Workbench(Gui.Workbench):
    MenuText = "AI Agent Bridge"
    ToolTip = "Interface for AI Agent Model Context Protocol Bridge"
    Icon = _icon_path()

    def Initialize(self):
        pass

    def GetClassName(self):
        return "Gui::PythonWorkbench"


def inject_ui():
    try:
        QtCore, QtGui, QtWidgets = _qt()
    except ImportError as e:
        App.Console.PrintError(f"[AI Bridge] UI injection skipped: {e}\n")
        return

    QTimer = QtCore.QTimer
    QMenu = QtWidgets.QMenu
    QToolBar = QtWidgets.QToolBar
    QIcon = QtGui.QIcon
    icon_path = _icon_path()

    mw = Gui.getMainWindow()
    if not mw:
        QTimer.singleShot(500, inject_ui)
        return

    menus = mw.menuBar().findChildren(QMenu)
    tools_menu = None
    for menu in menus:
        if menu.title().replace("&", "") == "Tools":
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
            action.triggered.connect(lambda: Gui.runCommand(_COMMAND))

    toolbars = mw.findChildren(QToolBar)
    target_tb = None
    for tb in toolbars:
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
        action.triggered.connect(lambda: Gui.runCommand(_COMMAND))


def _register_command():
    if _COMMAND in Gui.listCommands():
        try:
            Gui.removeCommand(_COMMAND)
        except Exception:
            pass
    Gui.addCommand(_COMMAND, AIAgentBridgeCommand())


def _register_workbench():
    if _WORKBENCH not in Gui.listWorkbenches():
        Gui.addWorkbench(FreeCAD_MCP_Bridge_Workbench())


def _schedule_ui():
    if getattr(App, _UI_SCHEDULED, False):
        return
    setattr(App, _UI_SCHEDULED, True)
    QtCore, _, _ = _qt()
    QtCore.QTimer.singleShot(1000, inject_ui)


_ensure_mod_path()
_register_command()
_register_workbench()
_schedule_ui()