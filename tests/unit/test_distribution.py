"""Requirements on what the addon distributes.

Each test name, and the first line of its docstring, states one requirement
as a user installing the addon experiences it. Addon Manager clones or
unpacks the release branch, whose commits hold the paths
scripts/build_release_commit.sh ships: a subset of the files git tracks on
main.
"""

import os
import re
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILD_SCRIPT = ROOT / "scripts" / "build_release_commit.sh"
REPOSITORY = "CREATeNG/freecad-mcp-bridge"
BINARY_SUFFIXES = {
    ".a", ".dll", ".dylib", ".exe", ".lib", ".node", ".o", ".obj", ".pyd",
    ".so", ".wasm",
}
BINARY_MAGIC = (
    b"\x7fELF",  # ELF: Linux, BSD
    b"MZ",  # PE: Windows .exe, .dll, .pyd
    b"\xfe\xed\xfa\xce",  # Mach-O, 32- and 64-bit, both byte orders
    b"\xfe\xed\xfa\xcf",
    b"\xce\xfa\xed\xfe",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",  # Mach-O universal
    b"\0asm",  # WebAssembly
)


def tracked_files():
    """The repository's tracked files, or None if git cannot list them."""
    try:
        listing = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=ROOT,
            capture_output=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    names = listing.decode("utf-8").split("\0")
    return [ROOT / name for name in names if name]


def is_machine_code(path):
    if path.suffix.lower() in BINARY_SUFFIXES:
        return True
    with open(path, "rb") as handle:
        head = handle.read(4)
    return head.startswith(BINARY_MAGIC)


def shipped_paths():
    """The SHIPPED list in build_release_commit.sh."""
    text = BUILD_SCRIPT.read_text(encoding="utf-8")
    block = re.search(r"^SHIPPED=\(\n(.*?)^\)", text, re.S | re.M)
    return [line.strip() for line in block[1].splitlines() if line.strip()]


def ships(relative):
    """Whether a path relative to the repository root is in a release."""
    relative = relative.replace("\\", "/").strip("/")
    return any(
        relative == path or relative.startswith(path + "/")
        for path in shipped_paths()
    )


def git_bash():
    """Git's bash, or None. On Windows, plain `bash` may be WSL's."""
    if os.name != "nt":
        return shutil.which("bash")
    git = shutil.which("git")
    if git is None:
        return None
    root = Path(git).resolve().parents[1]
    for candidate in (root / "bin" / "bash.exe", root / "usr" / "bin" / "bash.exe"):
        if candidate.is_file():
            return str(candidate)
    return None


class DistributionTests(unittest.TestCase):
    def test_the_addon_ships_as_source_with_no_prebuilt_binary(self):
        """The addon ships as source, with no prebuilt machine-code binary."""
        files = tracked_files()
        if files is None:
            self.skipTest("git cannot list the repository's files here")
        self.assertTrue(files)
        binaries = [
            str(path.relative_to(ROOT))
            for path in files
            if path.is_file() and is_machine_code(path)
        ]
        self.assertEqual(binaries, [])


class ReleaseCommitTests(unittest.TestCase):
    """The release commit holds what users install, and nothing else."""

    def test_a_release_carries_the_files_package_xml_names(self):
        """A release carries package.xml's icon, licence file and README."""
        root = ET.parse(ROOT / "package.xml").getroot()
        ns = {"p": "https://wiki.freecad.org/Package_Metadata"}
        named = [
            "package.xml",
            root.find("p:icon", ns).text,
            root.find("p:license", ns).get("file"),
        ]
        readme = next(
            url.text for url in root.findall("p:url", ns)
            if url.get("type") == "readme"
        )
        named.append(readme.rsplit("/release/", 1)[1])
        for path in named:
            with self.subTest(path=path):
                self.assertTrue(ships(path))
                self.assertTrue((ROOT / path).is_file())

    def test_a_link_in_a_shipped_document_stays_inside_the_release(self):
        """Each link from a shipped document to this repository's own files
        reaches a file the release carries: a relative link, or one to the
        release branch on GitHub."""
        on_release = re.compile(
            rf"https://(?:github\.com/{REPOSITORY}/blob|"
            rf"raw\.githubusercontent\.com/{REPOSITORY})/release/([^)#\s]+)"
        )
        for document in sorted(ROOT.glob("*.md")):
            if not ships(document.name):
                continue
            text = document.read_text(encoding="utf-8")
            targets = []
            for target in re.findall(r"\]\(([^)\s]+)\)", text):
                found = on_release.match(target)
                if found:
                    targets.append(found[1])
                elif not re.match(r"[a-z]+:|#", target):
                    targets.append(target.split("#", 1)[0])
            for target in targets:
                with self.subTest(document=document.name, target=target):
                    self.assertTrue(ships(target))
                    self.assertTrue((ROOT / target).exists())

    def test_tests_scripts_workflows_and_design_stay_on_main(self):
        """Tests, scripts, workflows, design documents, send_cmd.py and the
        developer guides are not in a release."""
        for path in (
            "tests/unit/test_distribution.py",
            "scripts/build_release_commit.sh",
            ".github/workflows/release.yml",
            "design/addon-hosted-mcp-server.md",
            "send_cmd.py",
            "DEVELOPMENT.md",
            "MAINTAINING.md",
            "TESTING.md",
        ):
            with self.subTest(path=path):
                self.assertFalse(ships(path))

    def test_the_release_commit_holds_the_shipped_files_on_the_last_release(self):
        """The script builds a commit holding only the shipped files from
        the main commit, with the previous release commit as its parent and
        a message naming the version and that main commit."""
        bash = git_bash()
        if bash is None or shutil.which("git") is None:
            self.skipTest("git and its bash are needed to build a commit")
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder)

            def git(*args):
                return subprocess.run(
                    ["git", *args],
                    cwd=repo,
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout.strip()

            git("init", "-q")
            git("config", "user.name", "test")
            git("config", "user.email", "test@example.invalid")
            git("config", "core.autocrlf", "false")
            (repo / "old.txt").write_text("previous release\n")
            git("add", "-A")
            git("commit", "-q", "-m", "previous release")
            parent = git("rev-parse", "HEAD")

            kept = []
            for path in shipped_paths():
                name = f"{path}/a.txt" if "." not in path else path
                (repo / name).parent.mkdir(parents=True, exist_ok=True)
                (repo / name).write_text(f"{path}\n")
                kept.append(name)
            for name in ("tests/unit/t.py", "scripts/s.sh", "DEVELOPMENT.md"):
                (repo / name).parent.mkdir(parents=True, exist_ok=True)
                (repo / name).write_text("main only\n")
            git("add", "-A")
            git("commit", "-q", "-m", "main")
            source = git("rev-parse", "HEAD")

            built = subprocess.run(
                [bash, str(BUILD_SCRIPT), source, parent, "9.9.9"],
                cwd=repo,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()

            files = git("ls-tree", "-r", "--name-only", built).splitlines()
            self.assertEqual(sorted(files), sorted(kept))
            self.assertEqual(git("rev-parse", f"{built}^"), parent)
            message = git("log", "-1", "--format=%B", built)
            self.assertTrue(message.startswith("Release v9.9.9\n"))
            self.assertIn(f"Built from main at {source}.", message)
            self.assertEqual(git("rev-parse", "HEAD"), source)
            self.assertEqual(git("status", "--porcelain"), "")


if __name__ == "__main__":
    unittest.main()
