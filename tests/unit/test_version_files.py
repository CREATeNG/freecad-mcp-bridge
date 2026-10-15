"""The three release-version files name one version."""

import json
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VERSION_FILES = (
    "package.xml",
    "mcp-stdio-shim/manifest.json",
    "mcp-stdio-shim/package.json",
)


def _read(root: Path, name: str) -> str | None:
    path = root / name
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def _package_xml_version(text: str) -> str:
    try:
        parsed = ET.fromstring(text)
    except ET.ParseError:
        return "(invalid package.xml)"
    for element in parsed.iter():
        tag = element.tag
        if tag != "version" and not tag.endswith("}version"):
            continue
        if element.text and element.text.strip():
            return element.text.strip()
    return "(no <version>)"


def _json_version(text: str) -> str:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return "(invalid JSON)"
    if not isinstance(data, dict):
        return "(no version)"
    value = data.get("version")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "(no version)"


def _release_versions(root: Path) -> dict[str, str]:
    """The version each release file names, keyed by its repo path."""
    found: dict[str, str] = {}
    for name in VERSION_FILES:
        text = _read(root, name)
        if text is None:
            found[name] = "(file missing)"
        elif name.endswith(".xml"):
            found[name] = _package_xml_version(text)
        else:
            found[name] = _json_version(text)
    return found


def _disagreement(found: dict[str, str]) -> str | None:
    """Each file and its version, when the three do not agree."""
    if len(set(found.values())) == 1:
        return None
    lines = ["The three version files disagree:"]
    for name in VERSION_FILES:
        lines.append(f"  {name}: {found[name]}")
    return "\n".join(lines)


class VersionFileTests(unittest.TestCase):
    def test_the_three_version_files_carry_the_same_release_version(self):
        """The three version files carry one release version."""
        problem = _disagreement(_release_versions(ROOT))
        self.assertIsNone(problem, problem)


if __name__ == "__main__":
    unittest.main()
