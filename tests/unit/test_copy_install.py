"""Checks for the copy-into-Mod install."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import test_install_common as common  # noqa: E402


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class CopyInstallTests(unittest.TestCase):
    def test_copy_places_the_checkout_without_git_or_github(self):
        """Copy mode places the checkout in Mod without .git or .github."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "checkout"
            target = root / "Mod" / "freecad-mcp-bridge"
            _write(source / "package.xml", "<package></package>")
            _write(source / "freecad" / "mcp_bridge" / "init_gui.py", "ok")
            _write(source / "README.md", "readme")
            _write(source / "scripts" / "ci_run_freecad.sh", "bash")
            _write(source / ".git" / "HEAD", "ref: refs/heads/main")
            _write(
                source / ".github" / "workflows" / "install-verify.yml",
                "on:",
            )
            _write(source / "freecad" / ".github" / "not-at-root.txt", "keep")

            common.copy_addon_checkout(str(source), str(target))

            self.assertEqual(
                (target / "freecad" / "mcp_bridge" / "init_gui.py").read_text(
                    encoding="utf-8"
                ),
                "ok",
            )
            self.assertTrue((target / "package.xml").is_file())
            self.assertTrue((target / "README.md").is_file())
            self.assertTrue(
                (target / "scripts" / "ci_run_freecad.sh").is_file()
            )
            self.assertTrue(
                (target / "freecad" / ".github" / "not-at-root.txt").is_file()
            )
            self.assertFalse((target / ".git").exists())
            self.assertFalse((target / ".github").exists())

    def test_checkout_source_is_the_workspace_when_set(self):
        """The copy's source is GITHUB_WORKSPACE when that is set."""
        previous = os.environ.get("GITHUB_WORKSPACE")
        os.environ["GITHUB_WORKSPACE"] = "C:/ci/checkout"
        try:
            self.assertEqual(common.checkout_source(), "C:/ci/checkout")
        finally:
            if previous is None:
                os.environ.pop("GITHUB_WORKSPACE", None)
            else:
                os.environ["GITHUB_WORKSPACE"] = previous

    def test_checkout_source_is_this_repo_without_a_workspace(self):
        """With no workspace set, the copy's source is this repo."""
        previous = os.environ.pop("GITHUB_WORKSPACE", None)
        try:
            self.assertEqual(common.checkout_source(), str(ROOT))
        finally:
            if previous is not None:
                os.environ["GITHUB_WORKSPACE"] = previous


if __name__ == "__main__":
    unittest.main()
