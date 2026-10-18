"""The release version is one version, with notes for that version.

Run `python tests/unit/test_version_files.py --body` to print the
release body, or exit with an error when the notes are not ready.
"""

import json
import re
import subprocess
import tempfile
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


STOCK_LINE = (
    "Source-only addon; the Claude Desktop bundle is attached as "
    "`freecad-mcp-bridge.mcpb`."
)
_SINCE = re.compile(r"\*\*What's new since (\d+\.\d+\.\d+)\*\*")
_HEADING = re.compile(r"## (\d+\.\d+\.\d+)")


class NotesError(Exception):
    """The changelog section is not ready to publish."""


def _tuple(text: str) -> tuple[int, int, int] | None:
    parts = text.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def _top_section(text: str) -> tuple[str, str] | str:
    """The newest `(version, body)`, or an error string."""
    lines = text.splitlines()
    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1
    if index >= len(lines):
        return "CHANGELOG.md has no version section"
    match = _HEADING.fullmatch(lines[index].strip())
    if match is None:
        return "CHANGELOG.md does not open with a ## x.y.z section"
    body: list[str] = []
    for line in lines[index + 1:]:
        if line.startswith("## "):
            break
        body.append(line)
    return match.group(1), "\n".join(body).strip("\n")


def _section_problem(version: str, body: str) -> str | None:
    if "<!--" in body:
        return "CHANGELOG.md still has a placeholder comment"
    since = None
    for line in body.splitlines():
        match = _SINCE.fullmatch(line.strip())
        if match is not None:
            since = match.group(1)
            break
    if since is None:
        return "CHANGELOG.md has no **What's new since {prev}** line"
    prev = _tuple(since)
    current = _tuple(version)
    if prev is None or current is None or not prev < current:
        return f"What's new since {since} is not lower than {version}"
    if not any(re.match(r"- \S", line) for line in body.splitlines()):
        return "CHANGELOG.md has no release-note bullet"
    if not any(line.startswith("Built ") for line in body.splitlines()):
        return "CHANGELOG.md has no Built credit line"
    return None


def notes_problem(root: Path) -> str | None:
    """Why `root`'s changelog cannot ship, or None when it can."""
    found = _release_versions(root)
    disagree = _disagreement(found)
    if disagree is not None:
        return disagree
    version = found["package.xml"]
    if version.startswith("("):
        return version
    text = _read(root, "CHANGELOG.md")
    if text is None:
        return "CHANGELOG.md is missing"
    section = _top_section(text)
    if isinstance(section, str):
        return section
    top, body = section
    if top != version:
        return (
            f"CHANGELOG.md starts at {top}, "
            f"but the version files say {version}"
        )
    return _section_problem(version, body)


def release_body(root: Path) -> str:
    """The top section, plus the stock bundle line."""
    problem = notes_problem(root)
    if problem is not None:
        raise NotesError(problem)
    text = _read(root, "CHANGELOG.md")
    assert text is not None
    section = _top_section(text)
    assert not isinstance(section, str)
    _version, body = section
    return f"{body}\n\n{STOCK_LINE}\n"


def _from_heading(text: str, heading: str) -> str:
    """`text` from the line `heading` starts, through the end."""
    for index, line in enumerate(text.splitlines(keepends=True)):
        if line.startswith(heading):
            return "".join(text.splitlines(keepends=True)[index:])
    raise AssertionError(f"{heading} is not in the changelog")


def _write_tree(root: Path, version: str, changelog: str) -> None:
    """Three version files and a changelog, all naming `version`."""
    (root / "mcp-stdio-shim").mkdir()
    (root / "package.xml").write_text(
        f"<package><version>{version}</version>"
        "<date>2026-01-01</date></package>\n",
        encoding="utf-8",
    )
    payload = f'{{"version": "{version}"}}\n'
    (root / "mcp-stdio-shim" / "manifest.json").write_text(
        payload, encoding="utf-8"
    )
    (root / "mcp-stdio-shim" / "package.json").write_text(
        payload, encoding="utf-8"
    )
    (root / "CHANGELOG.md").write_text(changelog, encoding="utf-8")


_GOOD = """## 1.2.3

**What's new since 1.2.2**

- A note.

Built by a team: example.
"""


class VersionFileTests(unittest.TestCase):
    def test_the_three_version_files_carry_the_same_release_version(self):
        """The three version files carry one release version."""
        problem = _disagreement(_release_versions(ROOT))
        self.assertIsNone(problem, problem)

    def test_the_changelog_section_matches_the_release_version(self):
        """The changelog's top section is this release, and ready."""
        problem = notes_problem(ROOT)
        self.assertIsNone(problem, problem)

    def test_the_release_body_matches_the_published_0_1_23_notes(self):
        """The 0.1.23 section still assembles the published notes."""
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        older = _from_heading(changelog, "## 0.1.23")
        expected = (
            ROOT / "tests" / "unit" / "v0.1.23-release-body.txt"
        ).read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_tree(root, "0.1.23", older)
            self.assertIsNone(notes_problem(root))
            self.assertEqual(release_body(root), expected)

    def test_a_newer_section_keeps_the_published_0_1_23_body(self):
        """A ready 0.1.24 section on top leaves the 0.1.23 body."""
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        newer = (
            "## 0.1.24\n"
            "\n"
            "**What's new since 0.1.23**\n"
            "\n"
            "- A later note.\n"
            "\n"
            "Built by a team: example.\n"
            "\n"
        ) + changelog
        expected = (
            ROOT / "tests" / "unit" / "v0.1.23-release-body.txt"
        ).read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            current = base / "current"
            current.mkdir()
            _write_tree(current, "0.1.24", newer)
            self.assertIsNone(notes_problem(current))
            published = base / "published"
            published.mkdir()
            _write_tree(
                published,
                "0.1.23",
                _from_heading(newer, "## 0.1.23"),
            )
            self.assertEqual(release_body(published), expected)


class ChangelogSectionTests(unittest.TestCase):
    def test_a_missing_changelog_is_refused(self):
        """A tree with no changelog section is refused."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_tree(root, "1.2.3", "")
            (root / "CHANGELOG.md").unlink()
            self.assertEqual(
                notes_problem(root), "CHANGELOG.md is missing"
            )

    def test_a_stale_top_section_is_refused(self):
        """A top section for an older version is refused."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_tree(root, "1.2.3", _GOOD.replace("1.2.3", "1.2.0", 1))
            problem = notes_problem(root)
            self.assertIsNotNone(problem)
            assert problem is not None
            self.assertIn("starts at 1.2.0", problem)

    def test_an_unfilled_stub_is_refused(self):
        """A placeholder comment, with no bullet, is refused."""
        notes = (
            "## 1.2.3\n"
            "\n"
            "**What's new since 1.2.2**\n"
            "\n"
            "<!-- Write the release notes for 1.2.3 as bullet points "
            "here and delete this comment. -->\n"
            "\n"
            "Built by a team: example.\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_tree(root, "1.2.3", notes)
            self.assertEqual(
                notes_problem(root),
                "CHANGELOG.md still has a placeholder comment",
            )

    def test_a_section_with_no_bullets_is_refused(self):
        """A credit line and a heading, with no bullet, is refused."""
        notes = """## 1.2.3

**What's new since 1.2.2**

Built by a team: example.
"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_tree(root, "1.2.3", notes)
            self.assertEqual(
                notes_problem(root),
                "CHANGELOG.md has no release-note bullet",
            )

    def test_a_good_section_assembles_the_release_body(self):
        """A ready section becomes the notes plus the stock line."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_tree(root, "1.2.3", _GOOD)
            self.assertIsNone(notes_problem(root))
            self.assertEqual(
                release_body(root),
                "**What's new since 1.2.2**\n"
                "\n"
                "- A note.\n"
                "\n"
                "Built by a team: example.\n"
                "\n"
                f"{STOCK_LINE}\n",
            )

    def test_the_bump_script_leaves_a_stub_that_fails_until_written(self):
        """The bump script starts the next section, unfinished."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_tree(root, "1.2.3", _GOOD)
            script = ROOT / "scripts" / "bump-package-z.sh"
            git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
            bash = str(git_bash) if git_bash.is_file() else "bash"
            subprocess.run(
                [bash, str(script)],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )
            bumped = _package_xml_version(
                (root / "package.xml").read_text(encoding="utf-8")
            )
            self.assertEqual(bumped, "1.2.4")
            problem = notes_problem(root)
            self.assertEqual(
                problem,
                "CHANGELOG.md still has a placeholder comment",
            )
            text = (root / "CHANGELOG.md").read_text(encoding="utf-8")
            self.assertTrue(text.startswith("## 1.2.4\n"))
            self.assertIn("**What's new since 1.2.3**", text)
            self.assertIn("Built by a team: example.", text)
            filled = text.replace(
                "<!-- Write the release notes for 1.2.4 as bullet "
                "points here and delete this comment. -->",
                "- A note.",
            )
            (root / "CHANGELOG.md").write_text(filled, encoding="utf-8")
            self.assertIsNone(notes_problem(root))


def main(argv: list[str]) -> int:
    """Print the release body, or run the tests."""
    import sys

    if "--body" in argv:
        try:
            sys.stdout.write(release_body(ROOT))
        except NotesError as err:
            print(err, file=sys.stderr)
            return 1
        return 0
    unittest.main()
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main(sys.argv[1:]))
