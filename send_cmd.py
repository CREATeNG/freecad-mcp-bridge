import sys
import os

try:
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtNetwork import QLocalSocket
except ImportError:
    try:
        from PySide2.QtCore import QCoreApplication
        from PySide2.QtNetwork import QLocalSocket
    except ImportError:
        from PySide.QtCore import QCoreApplication
        from PySide.QtNetwork import QLocalSocket

SOCKET_NAME = "freecad_mcp_bridge_socket"


def send_command(code, server_name=SOCKET_NAME):
    app = QCoreApplication.instance()
    if not app:
        app = QCoreApplication(sys.argv)

    socket = QLocalSocket()
    socket.connectToServer(server_name)
    
    # Wait up to 5 seconds to connect
    if not socket.waitForConnected(5000):
        print(f"Error: Connection to FreeCAD failed. Is the bridge running? ({socket.errorString()})")
        return False
        
    # Write Python code to the socket
    socket.write(code.encode("utf-8"))
    socket.flush() # Force write
    socket.waitForBytesWritten(1000) # Optional wait for sync Named Pipes
        
    # Wait for response from server
    if not socket.waitForReadyRead(10000):
        print("Error: Timeout waiting for FreeCAD to respond.")
        socket.disconnectFromServer()
        return False
        
    # Read response
    response = socket.readAll().data().decode("utf-8")
    
    print("\n--- FreeCAD Execution Output ---")
    print(response)
    print("--------------------------------")
    
    socket.disconnectFromServer()
    return True

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python send_cmd.py \"python_code_here\" or python send_cmd.py -f script_file.py")
        sys.exit(1)

    if sys.argv[1] == "-f":
        if len(sys.argv) < 3:
            print("Error: -f requires a script filepath")
            sys.exit(1)
        filepath = sys.argv[2]
        if not os.path.exists(filepath):
            print(f"Error: file not found: {filepath}")
            sys.exit(1)
        with open(filepath, "r", encoding="utf-8") as f:
            code = f.read()
    else:
        code = sys.argv[1]

    send_command(code)
