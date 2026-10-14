"""Requirements on what the addon distributes.

Each test name, and the first line of its docstring, states one requirement
as a user installing the addon experiences it. The addon is distributed as
its repository's files, which Addon Manager clones or unpacks, so the
files git tracks are what ships.
"""

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
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


if __name__ == "__main__":
    unittest.main()
