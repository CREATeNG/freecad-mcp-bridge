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

GET_OUTPUT_PAGE = {
    "name": "get_output_page",
    "description": (
        "Retrieve a page of output for a prior execute call that returned "
        "has_more=true. To read forward, pass page_no one higher than the "
        "last page_no you received (0 if you received none yet); repeat until "
        "has_more=false. Requesting a page_no already received replays it."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "job_token": {
                "type": "string",
                "description": (
                    "Token from a previous execute or get_output_page response."
                ),
            },
            "page_no": {
                "type": "integer",
                "minimum": 0,
                "description": "0-based index of the page to retrieve.",
            },
        },
        "required": ["job_token", "page_no"],
    },
}

TOOL_DEFINITIONS = [EXECUTE_PYTHON, EXECUTE_PYTHON_FILE, GET_OUTPUT_PAGE]
