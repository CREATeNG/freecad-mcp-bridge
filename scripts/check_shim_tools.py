"""Check that the Claude Desktop shim's bundled tool list matches the addon's.

The shim answers tools/list itself when FreeCAD isn't reachable, from
mcp-stdio-shim/tools.json. That copy must equal freecad/mcp_bridge/tools.py's
TOOL_DEFINITIONS. Run with --write to regenerate the copy.
"""

import json
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS_PY = ROOT / "freecad" / "mcp_bridge" / "tools.py"
TOOLS_JSON = ROOT / "mcp-stdio-shim" / "tools.json"


def main() -> int:
    expected = runpy.run_path(str(TOOLS_PY))["TOOL_DEFINITIONS"]
    if "--write" in sys.argv:
        TOOLS_JSON.write_text(json.dumps(expected, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Wrote {TOOLS_JSON.relative_to(ROOT)}")
        return 0
    actual = json.loads(TOOLS_JSON.read_text(encoding="utf-8"))
    if actual != expected:
        print(f"{TOOLS_JSON.relative_to(ROOT)} differs from {TOOLS_PY.relative_to(ROOT)}; run: python scripts/check_shim_tools.py --write")
        return 1
    print("Shim tool list matches the addon's.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
