"""Print the application's requirements: every acceptance test's requirement line.

The acceptance tests are the list of application requirements. Each test's
docstring opens with the requirement it guards; this prints those lines as
Markdown, one section per test module, in source order. It reads the test
files without importing them, so it needs no running bridge.

  python scripts/list_requirements.py [tests/acceptance]

Exits 1 if a test has no docstring, since its requirement would go unlisted.
"""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def first_line(node):
    doc = ast.get_docstring(node)
    return doc.strip().splitlines()[0] if doc else None


def main(argv):
    folder = Path(argv[1]) if len(argv) > 1 else ROOT / "tests" / "acceptance"
    lines = ["## Application requirements", ""]
    missing = []
    for path in sorted(folder.glob("test_*.py")):
        module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        lines += [f"### {first_line(module) or path.stem}", ""]
        for node in module.body:
            if not isinstance(node, ast.ClassDef):
                continue
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name.startswith("test"):
                    requirement = first_line(item)
                    if requirement is None:
                        missing.append(f"{path.name}::{node.name}.{item.name}")
                    else:
                        lines.append(f"- {requirement}")
        lines.append("")
    print("\n".join(lines))
    for name in missing:
        print(f"No requirement line (docstring) on {name}", file=sys.stderr)
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
