"""Registers the MCP Bridge command with FreeCAD.

FreeCAD names a command's category after the file that calls
Gui.addCommand, so that call lives here.
"""

import FreeCADGui as Gui


def register(command_id, command):
    Gui.addCommand(command_id, command)
