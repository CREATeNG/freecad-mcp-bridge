"""Requirements on the bridge's preferences (preferences.ui, config.py).

Each test name, and the first line of its docstring, states one requirement
as a user setting up the bridge experiences it. The preference page is read
from the .ui file FreeCAD builds it from; config.py is run against a
stand-in for FreeCAD's parameter store holding no values, as a fresh
profile does.
"""

import importlib
import sys
import types
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

from freecad.mcp_bridge.constants import PREF_GROUP

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "freecad" / "mcp_bridge" / "Resources" / "Ui" / "preferences.ui"
# Where a preference widget's prefPath is rooted in FreeCAD's parameters.
PREF_ROOT = "User parameter:BaseApp/Preferences"
SETTINGS = (
    "Port",
    "MaxResponseTimeoutSeconds",
    "MaxPageSize",
    "MaxHistoryRetentionMinutes",
)


def page_settings():
    """{prefEntry: {property name: text}} for each preference widget."""
    found = {}
    for widget in ET.parse(PAGE).iter("widget"):
        props = {
            prop.get("name"): prop[0].text
            for prop in widget.findall("property")
        }
        if "prefEntry" in props:
            found[props["prefEntry"]] = props
    return found


class EmptyParams:
    """FreeCAD's parameter group with nothing saved: every read gets its
    default."""

    def __init__(self):
        self.read = set()

    def GetInt(self, name, default):
        self.read.add(name)
        return default


def fresh_config():
    """Import config.py against an empty parameter store; return it, the
    store, and the groups it opened."""
    params, groups = EmptyParams(), []

    def param_get(group):
        groups.append(group)
        return params

    freecad = types.SimpleNamespace(ParamGet=param_get)
    with mock.patch.dict(sys.modules, {"FreeCAD": freecad}):
        sys.modules.pop("freecad.mcp_bridge.config", None)
        config = importlib.import_module("freecad.mcp_bridge.config")
    return config, params, groups


class PreferencePageTests(unittest.TestCase):
    def test_the_page_offers_port_timeout_page_size_and_retention(self):
        """Preferences offer port, timeout, page size and history retention."""
        settings = page_settings()
        self.assertEqual(sorted(settings), sorted(SETTINGS))
        # Each is saved where the bridge reads it.
        for name, props in settings.items():
            with self.subTest(setting=name):
                saved_in = f"{PREF_ROOT}/{props['prefPath']}"
                self.assertEqual(saved_in, PREF_GROUP)

    def test_the_page_shows_the_documented_defaults(self):
        """The page shows defaults 39280, 15 s, 64 K characters and 5 min."""
        settings = page_settings()
        shown = {
            name: (int(props["value"]), props.get("suffix"))
            for name, props in settings.items()
        }
        self.assertEqual(
            shown,
            {
                "Port": (39280, None),
                "MaxResponseTimeoutSeconds": (15, " s"),
                "MaxPageSize": (64, " K characters"),
                "MaxHistoryRetentionMinutes": (5, " min"),
            },
        )


class UnsetPreferenceTests(unittest.TestCase):
    def test_with_nothing_saved_the_bridge_uses_the_documented_defaults(self):
        """With nothing saved: port 39280, 15 s, 65,536 chars, 5 min."""
        config, params, groups = fresh_config()
        self.assertEqual(config.port(), 39280)
        self.assertEqual(config.max_response_timeout_ms(), 15_000)
        self.assertEqual(config.max_page_size_chars(), 65_536)
        self.assertEqual(config.max_history_retention_ms(), 5 * 60_000)
        # What it read is what the preference page saves.
        self.assertEqual(set(groups), {PREF_GROUP})
        self.assertEqual(params.read, set(SETTINGS))


if __name__ == "__main__":
    unittest.main()
