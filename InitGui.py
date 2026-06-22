import os
import sys
from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMenu, QToolBar
import FreeCADGui as Gui
import FreeCAD as App

class AIAgentBridgeCommand:
    def GetResources(self):
        # Use standard system preferences network icon
        return {
            'Pixmap': "preferences-system-network",
            'MenuText': 'Start/Stop AI Agent Bridge',
            'ToolTip': 'Toggle the local Named Pipe / UNIX socket bridge for AI agent control'
        }

    def Activated(self):
        try:
            # Import the bridge logic
            import freecad_bridge
            
            # Check if active
            inst = getattr(freecad_bridge, "_freecad_bridge_instance", None)
            if not inst:
                import __main__
                inst = getattr(__main__, "_freecad_bridge_instance", None)
                
            if inst and inst.isListening():
                inst.close()
                App.Console.PrintMessage("[AI Bridge] Stopped socket listener.\n")
                Gui.runCommand("StatusBar_Text", "AI Agent Bridge: Offline")
            else:
                if inst:
                    inst.close()
                freecad_bridge._freecad_bridge_instance = freecad_bridge.FreeCADBridge()
                App.Console.PrintMessage("[AI Bridge] Started socket listener.\n")
                Gui.runCommand("StatusBar_Text", "AI Agent Bridge: Listening...")
        except Exception as e:
            App.Console.PrintError(f"[AI Bridge] Error toggling bridge: {e}\n")

# Register the command with FreeCAD
try:
    Gui.addCommand("FreeCAD_MCP_Bridge_Toggle", AIAgentBridgeCommand())
except Exception as e:
    App.Console.PrintMessage(f"[AI Bridge] Command already registered: {e}\n")

def inject_ui():
    mw = Gui.getMainWindow()
    if not mw:
        # Retry shortly if main window is not fully initialized yet
        QTimer.singleShot(500, inject_ui)
        return
        
    # 1. Add to the Tools menu
    menus = mw.menuBar().findChildren(QMenu)
    tools_menu = None
    for menu in menus:
        if menu.title().replace("&", "") == "Tools":
            tools_menu = menu
            break
            
    if tools_menu:
        # Avoid duplicate actions on reload
        exists = False
        for action in tools_menu.actions():
            if action.text() == "Start/Stop AI Agent Bridge":
                exists = True
                break
        if not exists:
            tools_menu.addSeparator()
            action = tools_menu.addAction("Start/Stop AI Agent Bridge")
            action.triggered.connect(lambda: Gui.runCommand("FreeCAD_MCP_Bridge_Toggle"))
            
    # 2. Add to a persistent global toolbar
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
        action.setIcon(QIcon.fromTheme("preferences-system-network"))
        action.setToolTip("Toggle the local Named Pipe / UNIX socket bridge for AI agent control")
        action.triggered.connect(lambda: Gui.runCommand("FreeCAD_MCP_Bridge_Toggle"))

# Run injection slightly delayed after startup to ensure Qt GUI is loaded
QTimer.singleShot(1000, inject_ui)

class FreeCAD_MCP_Bridge_Workbench(Gui.Workbench):
    MenuText = "AI Agent Bridge"
    ToolTip = "Interface for AI Agent Model Context Protocol Bridge"
    Icon = "preferences-system-network"
    
    def Initialize(self):
        pass
        
    def GetClassName(self):
        return "FreeCAD_MCP_Bridge_Workbench"

Gui.addWorkbench(FreeCAD_MCP_Bridge_Workbench())
