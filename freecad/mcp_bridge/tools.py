"""MCP tool definitions exposed by the bridge.

Schemas only. `tools/list` returns these; the HTTP layer dispatches
`tools/call` by name.
"""

EXECUTE_PYTHON = {
    "name": "execute_python",
    "description": (
        "Execute Python code inside the running FreeCAD GUI process. "
        "'App'/'FreeCAD' and 'Gui'/'FreeCADGui' are pre-bound — do not import "
        "them. Captured stdout/stderr is returned."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Python source to execute in the FreeCAD session.",
            }
        },
        "required": ["code"],
    },
}

EXECUTE_PYTHON_FILE = {
    "name": "execute_python_file",
    "description": (
        "Read a local Python file and execute it inside the running FreeCAD "
        "GUI process. Same execution context as execute_python."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "filepath": {
                "type": "string",
                "description": "Absolute or workspace-relative path to a .py file.",
            }
        },
        "required": ["filepath"],
    },
}

GET_OUTPUT = {
    "name": "get_output",
    "description": (
        "Retrieve the next chunk of buffered output for a prior execute call "
        "that returned has_more=true. Pass the page_token from that response."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "page_token": {
                "type": "string",
                "description": "Token from a previous execute or get_output response.",
            }
        },
        "required": ["page_token"],
    },
}

TOOL_DEFINITIONS = [EXECUTE_PYTHON, EXECUTE_PYTHON_FILE, GET_OUTPUT]
