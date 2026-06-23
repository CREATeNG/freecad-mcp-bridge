"""FreeCAD MCP bridge — in-process socket listener and UI."""

from .bridge import FreeCADBridge, _bridge_instance

__all__ = ["FreeCADBridge", "_bridge_instance"]