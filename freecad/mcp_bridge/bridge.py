import sys
import traceback
from io import StringIO

import FreeCAD
from PySide.QtNetwork import QLocalServer, QLocalSocket


class FreeCADBridge(QLocalServer):
    def __init__(self, name="freecad_bridge_socket"):
        super().__init__()
        self.server_name = name
        self.newConnection.connect(self.handle_connection)

        QLocalServer.removeServer(self.server_name)

        if self.listen(self.server_name):
            FreeCAD.Console.PrintMessage(
                f"[Bridge] QLocalServer listening on '{self.server_name}'\n"
            )
            try:
                import FreeCADGui as Gui
                from PySide.QtWidgets import QDockWidget

                mw = Gui.getMainWindow()
                if mw:
                    dw = mw.findChild(QDockWidget, "Report view")
                    if dw and not dw.isVisible():
                        dw.show()
            except Exception:
                pass
        else:
            FreeCAD.Console.PrintError(
                f"[Bridge] Failed to start QLocalServer: {self.errorString()}\n"
            )

    def handle_connection(self):
        socket = self.nextPendingConnection()
        if not socket:
            return
        socket.readyRead.connect(lambda: self.read_code(socket))

    def read_code(self, socket):
        data = socket.readAll()
        code = data.data().decode("utf-8")

        import FreeCADGui

        old_stdout = sys.stdout
        old_stderr = sys.stderr
        redirected_output = StringIO()
        sys.stdout = redirected_output
        sys.stderr = redirected_output

        locs = {
            "FreeCAD": FreeCAD,
            "App": FreeCAD,
            "FreeCADGui": FreeCADGui,
            "Gui": FreeCADGui,
        }

        error = None
        try:
            exec(code, globals(), locs)
        except Exception:
            error = traceback.format_exc()

        sys.stdout = old_stdout
        sys.stderr = old_stderr

        output_str = redirected_output.getvalue()
        if error:
            output_str += f"\n--- EXCEPTION ---\n{error}"

        if output_str:
            FreeCAD.Console.PrintMessage(f"[Bridge Executed]:\n{output_str}\n")
        if error:
            FreeCAD.Console.PrintError(f"[Bridge Exception]:\n{error}\n")

        socket.write(output_str.encode("utf-8"))
        socket.disconnectFromServer()


_bridge_instance = None


if __name__ == "__main__":
    try:
        import __main__

        if hasattr(__main__, "_freecad_bridge_instance"):
            old_inst = getattr(__main__, "_freecad_bridge_instance")
            if hasattr(old_inst, "close"):
                old_inst.close()
            if hasattr(old_inst, "timer"):
                old_inst.timer.stop()
            print("[Bridge] Closed/Stopped existing local listener in __main__.")
    except Exception as e:
        print(f"[Bridge] Error closing old listener: {e}")

    _bridge_instance = FreeCADBridge()