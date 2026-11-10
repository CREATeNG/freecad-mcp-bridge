"""The install scripts refuse a token folder in the home directory."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import test_install_common as common  # noqa: E402
from freecad.mcp_bridge import token_file  # noqa: E402


class TokenDirGuardTests(unittest.TestCase):
    def test_the_guard_uses_the_same_folder_as_token_file(self):
        """bridge_token_dir matches token_file.token_dir."""
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.dict(
                os.environ, {"FREECAD_MCP_TOKEN_DIR": folder}
            ):
                self.assertEqual(
                    common.bridge_token_dir(), token_file.token_dir()
                )
        with mock.patch.dict(os.environ, {"FREECAD_MCP_TOKEN_DIR": ""}):
            self.assertEqual(
                common.bridge_token_dir(), token_file.token_dir()
            )

    def test_an_unset_token_dir_is_refused(self):
        """No FREECAD_MCP_TOKEN_DIR, and a blank one, both refuse."""
        expected = (
            "FREECAD_MCP_TOKEN_DIR is not set, so the bridge would use "
            "the home .freecad-mcp-bridge folder. Set it to another folder."
        )
        with mock.patch.dict(os.environ):
            os.environ.pop("FREECAD_MCP_TOKEN_DIR", None)
            self.assertEqual(common.token_dir_refusal(), expected)
            with mock.patch.object(common, "fail") as fail:
                common.require_isolated_token_dir()
        fail.assert_called_once_with(expected)
        for value in ("", "   "):
            with mock.patch.dict(
                os.environ, {"FREECAD_MCP_TOKEN_DIR": value}
            ):
                self.assertEqual(common.token_dir_refusal(), expected)
                with mock.patch.object(common, "fail") as fail:
                    common.require_isolated_token_dir()
            fail.assert_called_once_with(expected)

    def test_the_home_bridge_folder_is_refused(self):
        """The home .freecad-mcp-bridge folder is refused, slash for slash."""
        home_folder = Path.home() / ".freecad-mcp-bridge"
        spelled = str(home_folder).replace("\\", "/")
        with mock.patch.dict(
            os.environ, {"FREECAD_MCP_TOKEN_DIR": spelled}
        ):
            message = common.token_dir_refusal()
        self.assertEqual(
            message,
            "FREECAD_MCP_TOKEN_DIR is the home .freecad-mcp-bridge "
            "folder. Set it to another folder.",
        )
        with mock.patch.dict(
            os.environ, {"FREECAD_MCP_TOKEN_DIR": spelled}
        ):
            with mock.patch.object(common, "fail") as fail:
                common.require_isolated_token_dir()
        fail.assert_called_once_with(message)

    def test_another_folder_is_allowed(self):
        """A folder other than the home one does not refuse."""
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.dict(
                os.environ, {"FREECAD_MCP_TOKEN_DIR": folder}
            ):
                self.assertEqual(common.token_dir_refusal(), "")
                with mock.patch.object(common, "fail") as fail:
                    common.require_isolated_token_dir()
            fail.assert_not_called()


if __name__ == "__main__":
    unittest.main()
