"""The test start stores a bearer token before the toolbar click."""

import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import test_install_common as common  # noqa: E402


class ToolbarStartTokenTests(unittest.TestCase):
    def test_store_token_for_toolbar_start_stores_one(self):
        """The helper stores a token through config and returns it."""
        stored = []

        def generate():
            stored.append("made")
            return "made"

        pkg = types.ModuleType("freecad.mcp_bridge")
        pkg.config = types.SimpleNamespace(
            generate_and_store_token=generate
        )
        freecad = types.ModuleType("freecad")
        freecad.mcp_bridge = pkg
        with mock.patch.dict(
            sys.modules,
            {
                "freecad": freecad,
                "freecad.mcp_bridge": pkg,
            },
        ):
            token = common.store_token_for_toolbar_start()
        self.assertEqual(token, "made")
        self.assertEqual(stored, ["made"])

    def test_the_serve_and_verify_store_a_token_before_the_click(self):
        """acceptance_serve and test_verify store a token, then start."""
        for name in ("acceptance_serve.py", "test_verify.py"):
            text = (ROOT / "scripts" / name).read_text(encoding="utf-8")
            store_at = text.index("store_token_for_toolbar_start()")
            start_at = text.index("common.start_bridge()")
            self.assertLess(store_at, start_at, name)


if __name__ == "__main__":
    unittest.main()
