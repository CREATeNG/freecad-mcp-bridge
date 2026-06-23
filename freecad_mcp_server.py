import sys
import os
from PySide6.QtCore import QCoreApplication
from PySide6.QtNetwork import QLocalSocket
from mcp.server.fastmcp import FastMCP

# Initialize FastMCP Server
mcp = FastMCP("MCP Bridge")

SOCKET_NAME = "freecad_mcp_bridge_socket"


def send_to_socket(code: str, server_name=SOCKET_NAME) -> str:
    app = QCoreApplication.instance()
    if not app:
        app = QCoreApplication(sys.argv)

    socket = QLocalSocket()
    socket.connectToServer(server_name)
    
    # Wait up to 5 seconds to connect
    if not socket.waitForConnected(5000):
        return f"Error: Connection to FreeCAD failed. Is the bridge running in FreeCAD? ({socket.errorString()})"
        
    # Write Python code to the socket
    socket.write(code.encode("utf-8"))
    socket.flush()
    socket.waitForBytesWritten(1000) # Wait for synchronous named pipe writing
    
    # Wait for response from server
    if not socket.waitForReadyRead(15000): # 15 seconds timeout
        socket.disconnectFromServer()
        return "Error: Timeout waiting for FreeCAD to respond."
        
    response = socket.readAll().data().decode("utf-8")
    socket.disconnectFromServer()
    return response

@mcp.tool()
def execute_python(code: str) -> str:
    """
    Execute arbitrary Python code inside the running FreeCAD GUI process.
    Provides full programmatic access to FreeCAD's document structure (App) and GUI (Gui).

    Guidelines for AI Agents:
    1. The variables 'App' (FreeCAD) and 'Gui' (FreeCADGui) are pre-loaded in your context. You do NOT need to import them.
    2. Always check if App.ActiveDocument is None. If None, create one using:
       doc = App.ActiveDocument or App.newDocument("Unnamed")
    3. Always call App.ActiveDocument.recompute() after adding, modifying, or deleting geometry to update the 3D viewer.
    4. For visual feedback, save a viewport dump to the system temp folder:
       import os, tempfile
       screenshot = os.path.join(tempfile.gettempdir(), "freecad_screenshot.png")
       Gui.activeView().saveImage(screenshot, 1920, 1080, 'Current')
    """
    return send_to_socket(code)

@mcp.tool()
def execute_python_file(filepath: str) -> str:
    """
    Read a local Python script file from your disk and execute it inside FreeCAD.
    Allows running complex, pre-written script files.

    Guidelines for AI Agents:
    1. The path can be absolute or relative to the workspace Cwd.
    2. The target python file must contain standard FreeCAD scripting code.
    3. Ensure the script calls App.ActiveDocument.recompute() at the end to refresh the 3D GUI.
    """
    if not os.path.exists(filepath):
        return f"Error: File not found at '{filepath}'"
        
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            code = f.read()
        return send_to_socket(code)
    except Exception as e:
        return f"Error reading file '{filepath}': {str(e)}"

if __name__ == "__main__":
    mcp.run()
