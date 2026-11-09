"""Requirements on the bridge's preferences (preferences.ui, config.py).

Each test name, and the first line of its docstring, states one requirement
as a user setting up the bridge experiences it. The preference page is read
from the .ui file FreeCAD builds it from; config.py is run against a
stand-in for FreeCAD's parameter store holding no values, as a fresh
profile does.
"""

import importlib
import json
import os
import re
import stat
import sys
import tempfile
import types
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

from freecad.mcp_bridge.constants import (
    COMMAND_ID,
    DISPLAY_NAME,
    PREF_GROUP,
    TOOLBAR_OBJECT_NAME,
)
from freecad.mcp_bridge.file_prompt import MODE_TOOLTIP

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "freecad" / "mcp_bridge" / "Resources" / "Ui" / "preferences.ui"
# Where a preference widget's prefPath is rooted in FreeCAD's parameters.
PREF_ROOT = "User parameter:BaseApp/Preferences"
SETTINGS = (
    "ListenAddress",
    "RequireTls",
    "TlsCertFile",
    "TlsKeyFile",
    "MaxResponseTimeoutSeconds",
    "MaxPageSize",
    "MaxHistoryRetentionMinutes",
    "ShowStatusButton",
    "StatusButtonPosition",
    "PermissionMode",
)


def page_settings():
    """{prefEntry: {property name: text}} for each preference widget."""
    found = {}
    for widget in ET.parse(PAGE).iter("widget"):
        props = {
            prop.get("name"): prop[0].text
            for prop in widget.findall("property")
        }
        items = [
            item.find("property/string").text
            for item in widget.findall("item")
        ]
        if items:
            props["items"] = items
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

    def GetBool(self, name, default):
        self.read.add(name)
        return default

    def GetString(self, name, default=""):
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
        """Preferences offer port, timeout, page size, history retention and
        the status-bar button controls."""
        settings = page_settings()
        self.assertEqual(sorted(settings), sorted(SETTINGS))
        # Each is saved where the bridge reads it.
        for name, props in settings.items():
            with self.subTest(setting=name):
                saved_in = f"{PREF_ROOT}/{props['prefPath']}"
                self.assertEqual(saved_in, PREF_GROUP)

    def test_the_page_shows_the_documented_defaults(self):
        """The page shows defaults 127.0.0.1:39280, 15 s, 32 K characters,
        5 min, and the status-bar button shown on the right."""
        settings = page_settings()
        self.assertEqual(
            settings["ListenAddress"]["plainText"], "127.0.0.1:39280"
        )
        self.assertEqual(
            settings["ListenAddress"]["placeholderText"], "127.0.0.1:39280"
        )
        self.assertNotIn("RequireToken", settings)
        self.assertNotIn("RequireOnLoopback", settings)
        gen_btn = next(
            w
            for w in ET.parse(PAGE).iter("widget")
            if w.get("name") == "generateTokenButton"
        )
        gen_props = {
            prop.get("name"): prop[0].text
            for prop in gen_btn.findall("property")
        }
        self.assertEqual(gen_props["text"], "Generate new token")
        self.assertEqual(
            gen_props["toolTip"],
            "Generate a new bearer token and show it once.",
        )
        self.assertEqual(
            settings["RequireTls"]["text"], "Require TLS (HTTPS)"
        )
        self.assertEqual(
            settings["RequireTls"]["toolTip"],
            "Serve HTTPS on non-loopback listen addresses with the files "
            "below.",
        )
        self.assertEqual(settings["RequireTls"]["checked"], "true")
        self.assertEqual(
            settings["PermissionMode"]["items"],
            ["Ask (default)", "Unrestricted"],
        )
        self.assertEqual(settings["PermissionMode"]["currentIndex"], "0")
        self.assertEqual(
            settings["PermissionMode"]["toolTip"], MODE_TOOLTIP
        )
        self.assertEqual(
            MODE_TOOLTIP,
            "Ask: FreeCAD shows you an agent's code before it runs. "
            "Unrestricted: any code an agent sends runs with your full "
            "permissions, unseen.",
        )
        self.assertIn(settings["TlsCertFile"]["fileName"], (None, ""))
        self.assertIn(settings["TlsKeyFile"]["fileName"], (None, ""))
        pem_filter = "PEM files (*.pem *.crt *.key);;All files (*)"
        self.assertEqual(settings["TlsCertFile"]["filter"], pem_filter)
        self.assertEqual(settings["TlsKeyFile"]["filter"], pem_filter)
        self.assertEqual(
            settings["TlsCertFile"]["toolTip"],
            "PEM certificate file for HTTPS.",
        )
        self.assertEqual(
            settings["TlsKeyFile"]["toolTip"],
            "PEM private key file for HTTPS.",
        )
        widgets_by_name = {
            w.get("name"): {
                prop.get("name"): prop[0].text
                for prop in w.findall("property")
            }
            for w in ET.parse(PAGE).iter("widget")
        }
        self.assertEqual(
            widgets_by_name["securityGroup"]["title"],
            "Security",
        )
        self.assertEqual(
            widgets_by_name["permissionsGroup"]["title"],
            "Permissions",
        )
        self.assertEqual(
            widgets_by_name["permissionModeLabel"]["text"],
            "Permission mode",
        )
        self.assertEqual(
            widgets_by_name["tlsCertFileLabel"]["text"],
            "TLS certificate file (PEM)",
        )
        self.assertEqual(
            widgets_by_name["tlsKeyFileLabel"]["text"],
            "TLS private key file (PEM)",
        )
        self.assertEqual(
            widgets_by_name["serverHint"]["text"],
            "To start or stop the bridge, click <b>MCP On/Off</b> "
            "in the status bar. While active, it listens "
            "on the addresses below.",
        )
        self.assertIn(
            "<nobr>https:&#x2060;//&#x2060;&lt;ADDRESS&gt;&#x2060;:"
            "&#x2060;&lt;PORT&gt;/mcp</nobr>. "
            "Every listener requires the bearer token.",
            widgets_by_name["clientNote"]["text"],
        )
        spin_names = (
            "MaxResponseTimeoutSeconds",
            "MaxPageSize",
            "MaxHistoryRetentionMinutes",
        )
        shown = {
            name: (int(settings[name]["value"]), settings[name].get("suffix"))
            for name in spin_names
        }
        self.assertEqual(
            shown,
            {
                "MaxResponseTimeoutSeconds": (15, " s"),
                "MaxPageSize": (32, " K characters"),
                "MaxHistoryRetentionMinutes": (5, " min"),
            },
        )
        self.assertEqual(
            settings["ShowStatusButton"]["text"], "Show the On/Off button"
        )
        self.assertEqual(settings["ShowStatusButton"]["checked"], "true")
        self.assertEqual(
            settings["StatusButtonPosition"]["currentIndex"], "0"
        )
        self.assertEqual(
            settings["StatusButtonPosition"]["items"],
            [
                "Right of FreeCAD's items (default)",
                "Left of FreeCAD's items",
            ],
        )


class UnsetPreferenceTests(unittest.TestCase):
    def test_with_nothing_saved_the_bridge_uses_the_documented_defaults(self):
        """With nothing saved: 127.0.0.1:39280, 15 s, 32,768 chars, 5 min,
        button shown on the right."""
        config, params, groups = fresh_config()
        self.assertEqual(config.listen_addresses(), ["127.0.0.1:39280"])
        self.assertIs(config.require_tls(), True)
        self.assertEqual(config.tls_cert_file(), "")
        self.assertEqual(config.tls_key_file(), "")
        self.assertEqual(config.max_response_timeout_ms(), 15_000)
        self.assertEqual(config.max_page_size_chars(), 32_768)
        self.assertEqual(config.max_history_retention_ms(), 5 * 60_000)
        self.assertIs(config.show_status_button(), True)
        self.assertEqual(config.status_button_position(), 0)
        self.assertIs(config.unrestricted(), False)
        # What it read is what the preference page saves (plus Port as the
        # upgrade fallback when ListenAddress has never been saved).
        self.assertEqual(set(groups), {PREF_GROUP})
        self.assertEqual(params.read, set(SETTINGS) | {"Port"})


def _fresh_init_gui():
    """Import init_gui.py against stand-in FreeCAD and Qt modules."""
    freecad = types.SimpleNamespace(
        Console=types.SimpleNamespace(PrintWarning=lambda *_: None)
    )
    gui = types.SimpleNamespace(
        addIconPath=lambda *_: None,
        listCommands=lambda: [],
        addCommand=lambda *_: None,
        addPreferencePage=lambda *_: None,
        addWorkbenchManipulator=lambda *_: None,
    )
    qtcore = types.SimpleNamespace(
        QTimer=types.SimpleNamespace(singleShot=lambda *_: None)
    )
    stubs = {
        "FreeCAD": freecad,
        "FreeCADGui": gui,
        "PySide": types.SimpleNamespace(QtCore=qtcore),
        "PySide.QtCore": qtcore,
        "freecad.mcp_bridge.bridge": types.SimpleNamespace(),
    }
    with mock.patch.dict(sys.modules, stubs):
        sys.modules.pop("freecad.mcp_bridge.init_gui", None)
        module = importlib.import_module("freecad.mcp_bridge.init_gui")
        sys.modules.pop("freecad.mcp_bridge.init_gui", None)
    return module


def _hex_to_rgb(hex_color):
    value = hex_color.lstrip("#")
    return (
        int(value[0:2], 16),
        int(value[2:4], 16),
        int(value[4:6], 16),
    )


def _contrast_ratio(rgb_a, rgb_b):
    def _lum(rgb):
        lin = [
            (c / 255.0) / 12.92
            if c / 255.0 <= 0.04045
            else ((c / 255.0 + 0.055) / 1.055) ** 2.4
            for c in rgb
        ]
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]

    lum_a, lum_b = _lum(rgb_a), _lum(rgb_b)
    hi, lo = max(lum_a, lum_b), min(lum_a, lum_b)
    return (hi + 0.05) / (lo + 0.05)


class StatusButtonColorTests(unittest.TestCase):
    def test_on_green_suits_dark_and_light_status_bar_themes(self):
        """The 'On' button picks a brighter green on dark status bars and a
        darker green on light ones, reaching at least 4.5:1 on each."""
        init_gui = _fresh_init_gui()
        dark_bars = (
            "#1e1e1e",
            "#2b2b2b",
            "#303030",
            "#404040",
            "#444444",
        )
        light_bars = ("#efefef", "#ffffff")
        for bg_hex in dark_bars:
            with self.subTest(bg=bg_hex):
                bg_rgb = _hex_to_rgb(bg_hex)
                picked = init_gui._status_button_on_color(bg_rgb)
                self.assertEqual(picked, "#56d364")
                ratio = _contrast_ratio(_hex_to_rgb(picked), bg_rgb)
                self.assertGreaterEqual(ratio, 4.5)
        for bg_hex in light_bars:
            with self.subTest(bg=bg_hex):
                bg_rgb = _hex_to_rgb(bg_hex)
                picked = init_gui._status_button_on_color(bg_rgb)
                self.assertEqual(picked, "#116329")
                ratio = _contrast_ratio(_hex_to_rgb(picked), bg_rgb)
                self.assertGreaterEqual(ratio, 4.5)


class StatusBarWidgetsTests(unittest.TestCase):
    def test_status_bar_widgets_splits_at_stretch_and_rejects_unknown_schema(
        self,
    ):
        """Status-bar widget lookup splits normal from permanent widgets by
        layout index and returns (None, None) when the row schema is unknown.
        """
        init_gui = _fresh_init_gui()

        class FakeLayout:
            def __init__(self, parent=None):
                self._parent = parent

            def parent(self):
                return self._parent

        class FakeHBox(FakeLayout):
            def __init__(self, parent=None, total=3, indices=None):
                super().__init__(parent)
                self._total = total
                self._indices = indices or {}

            def count(self):
                return self._total

            def indexOf(self, widget):
                return self._indices.get(widget, -1)

        class FakeWidget:
            pass

        class FakeBar:
            def __init__(self, layouts, widgets):
                self._layouts = layouts
                self._widgets = widgets

            def findChildren(self, cls, options=None):
                if cls is FakeHBox:
                    return list(self._layouts)
                if cls is FakeWidget and options == "direct":
                    return list(self._widgets)
                return []

        qtcore = types.SimpleNamespace(
            Qt=types.SimpleNamespace(FindDirectChildrenOnly="direct")
        )
        qtwidgets = types.SimpleNamespace(
            QHBoxLayout=FakeHBox, QLayout=FakeLayout, QWidget=FakeWidget
        )
        stubs = {
            "PySide": types.SimpleNamespace(
                QtCore=qtcore, QtWidgets=qtwidgets
            ),
            "PySide.QtCore": qtcore,
            "PySide.QtWidgets": qtwidgets,
        }
        n1, p1, p2 = FakeWidget(), FakeWidget(), FakeWidget()
        with mock.patch.dict(sys.modules, stubs):
            bar = FakeBar([], [n1, p1])
            self.assertEqual(init_gui._status_bar_widgets(bar), (None, None))

            vbox = FakeLayout()
            row = FakeHBox(
                parent=vbox,
                total=6,
                indices={n1: 1, p1: 3, p2: 4},
            )
            bar_ok = FakeBar([row], [n1, p1, p2])
            vbox._parent = bar_ok
            child_hbox = FakeHBox(parent=n1, total=1, indices={})
            bar_ok._layouts.append(child_hbox)
            self.assertEqual(
                init_gui._status_bar_widgets(bar_ok), ([n1], [p1, p2])
            )

            bad_gap = FakeHBox(
                parent=vbox,
                total=7,
                indices={n1: 1, p1: 3, p2: 5},
            )
            bar_gap = FakeBar([bad_gap], [n1, p1, p2])
            vbox._parent = bar_gap
            self.assertEqual(
                init_gui._status_bar_widgets(bar_gap), (None, None)
            )

            empty_row = FakeHBox(parent=vbox, total=3, indices={})
            bar_empty = FakeBar([empty_row], [])
            vbox._parent = bar_empty
            self.assertEqual(
                init_gui._status_bar_widgets(bar_empty), ([], [])
            )

            normal_only = FakeHBox(
                parent=vbox, total=4, indices={n1: 1}
            )
            bar_normal = FakeBar([normal_only], [n1])
            vbox._parent = bar_normal
            self.assertEqual(
                init_gui._status_bar_widgets(bar_normal), ([n1], [])
            )

            collapsed_one = FakeHBox(
                parent=vbox, total=3, indices={n1: 1}
            )
            bar_c1 = FakeBar([collapsed_one], [n1])
            vbox._parent = bar_c1
            self.assertEqual(
                init_gui._status_bar_widgets(bar_c1), (None, None)
            )

            collapsed_two = FakeHBox(
                parent=vbox, total=3, indices={n1: 1, p1: 2}
            )
            bar_c2 = FakeBar([collapsed_two], [n1, p1])
            vbox._parent = bar_c2
            self.assertEqual(
                init_gui._status_bar_widgets(bar_c2), (None, None)
            )


class ToolbarVisibilityTests(unittest.TestCase):
    def test_manipulator_appends_toolbar_and_command_except_on_none_workbench(
        self,
    ):
        """MCPBridgeUiManipulator appends the MCP Bridge toolbar under the
        root item and the toggle command under it, skipping NoneWorkbench."""
        init_gui = _fresh_init_gui()
        manipulator = init_gui.MCPBridgeUiManipulator()
        scheduled = []

        class NoneWorkbench:
            pass

        class PartDesignWorkbench:
            pass

        active_wb = None
        freecad = types.SimpleNamespace(
            MCPBridgeCommand=COMMAND_ID,
            MCPBridgeInjectUi=init_gui.inject_ui,
        )
        gui = types.SimpleNamespace(activeWorkbench=lambda: active_wb)
        qtcore = types.SimpleNamespace(
            QTimer=types.SimpleNamespace(
                singleShot=lambda delay, fn: scheduled.append((delay, fn))
            )
        )
        stubs = {
            "FreeCAD": freecad,
            "FreeCADGui": gui,
            "PySide": types.SimpleNamespace(QtCore=qtcore),
            "PySide.QtCore": qtcore,
        }
        with mock.patch.dict(sys.modules, stubs):
            self.assertEqual(manipulator.modifyToolBars(), [])
            self.assertEqual(scheduled, [])

            active_wb = NoneWorkbench()
            self.assertEqual(manipulator.modifyToolBars(), [])
            self.assertEqual(scheduled, [])

            active_wb = PartDesignWorkbench()
            self.assertEqual(
                manipulator.modifyToolBars(),
                [
                    {"append": DISPLAY_NAME, "toolBar": ""},
                    {"append": COMMAND_ID, "toolBar": DISPLAY_NAME},
                ],
            )
            self.assertEqual(scheduled, [(0, init_gui.inject_ui)])

    def test_command_action_tracks_state_without_changing_visibility(self):
        """show_toggle_state updates the command's own action, and inject_ui
        reports readiness without creating a toolbar or altering visibility."""
        messages = []

        class FakeAction:
            def __init__(self):
                self._checkable = False
                self._checked = False
                self._tooltip = ""

            def isCheckable(self):
                return self._checkable

            def setCheckable(self, checkable):
                self._checkable = bool(checkable)

            def setChecked(self, checked):
                self._checked = bool(checked)

            def isChecked(self):
                return self._checked

            def setToolTip(self, tip):
                self._tooltip = tip

            def toolTip(self):
                return self._tooltip

        class FakeCommand:
            def __init__(self, actions):
                self._actions = list(actions)

            def getAction(self):
                return list(self._actions)

        class FakeToolBar:
            def __init__(self, title, visible=True):
                self._title = title
                self._object_name = TOOLBAR_OBJECT_NAME
                self._visible = visible

            def windowTitle(self):
                return self._title

            def objectName(self):
                return self._object_name

            def isVisible(self):
                return self._visible

            def setVisible(self, visible):
                self._visible = bool(visible)

        class FakeMainWindow:
            def __init__(self, toolbars):
                self._toolbars = list(toolbars)

            def findChildren(self, cls):
                if cls is FakeToolBar:
                    return list(self._toolbars)
                return []

            def update(self):
                pass

            def isVisible(self):
                return True

        init_gui = _fresh_init_gui()
        action = FakeAction()
        command_wrapper = FakeCommand([action])
        tb = FakeToolBar(DISPLAY_NAME, visible=True)
        current_mw = FakeMainWindow([tb])
        running = False

        freecad = types.SimpleNamespace(
            MCPBridgeCommand=COMMAND_ID,
            MCPBridgeInjectUi=init_gui.inject_ui,
            Console=types.SimpleNamespace(
                PrintMessage=messages.append,
                PrintWarning=lambda *_: None,
            ),
        )
        gui = types.SimpleNamespace(
            getMainWindow=lambda: current_mw,
            Command=types.SimpleNamespace(
                get=lambda name: command_wrapper
                if name == COMMAND_ID
                else None
            ),
        )
        qtcore = types.SimpleNamespace(
            QTimer=types.SimpleNamespace(singleShot=lambda *_: None)
        )
        qtwidgets = types.SimpleNamespace(QToolBar=FakeToolBar)
        stubs = {
            "FreeCAD": freecad,
            "FreeCADGui": gui,
            "PySide": types.SimpleNamespace(
                QtCore=qtcore, QtWidgets=qtwidgets
            ),
            "PySide.QtCore": qtcore,
            "PySide.QtWidgets": qtwidgets,
        }

        with (
            mock.patch.dict(sys.modules, stubs),
            mock.patch.object(init_gui, "_ensure_status_button"),
            mock.patch.object(init_gui, "_refresh_status_button"),
            mock.patch.object(
                init_gui.bridge,
                "is_running",
                side_effect=lambda: running,
                create=True,
            ),
        ):
            self.assertTrue(init_gui.inject_ui())
            self.assertTrue(action.isCheckable())
            self.assertFalse(action.isChecked())
            self.assertEqual(
                action.toolTip(), "MCP Bridge: off (click to start)"
            )
            self.assertEqual(messages, ["[MCP Bridge] Toolbar ready.\n"])

            running = True
            init_gui.show_toggle_state()
            self.assertTrue(action.isChecked())
            self.assertEqual(
                action.toolTip(), "MCP Bridge: on (click to stop)"
            )

            tb.setVisible(False)
            self.assertFalse(init_gui.inject_ui())
            self.assertFalse(tb.isVisible())

    def test_command_is_registered_under_the_mcp_bridge_category(self):
        """MCP_Bridge.register calls Gui.addCommand from MCP_Bridge.py, so
        the Customize category reads 'MCP_Bridge'."""
        recorded = []

        def add_command(name, cmd):
            fn = sys._getframe(1).f_code.co_filename.replace("\\", "/")
            tail = fn.rsplit("/", 1)[-1]
            module = tail.rsplit(".", 1)[0] if "." in tail else tail
            match = re.search(r"/Mod/(\w+)/", fn) or re.search(
                r"/Ext/freecad/(\w+)/", fn
            )
            group = match.group(1) if match else module
            recorded.append((name, group, cmd))

        freecad = types.SimpleNamespace(
            Console=types.SimpleNamespace(PrintWarning=lambda *_: None)
        )
        gui = types.SimpleNamespace(
            addIconPath=lambda *_: None,
            listCommands=lambda: [],
            addCommand=add_command,
            addPreferencePage=lambda *_: None,
            addWorkbenchManipulator=lambda *_: None,
        )
        qtcore = types.SimpleNamespace(
            QTimer=types.SimpleNamespace(singleShot=lambda *_: None)
        )
        stubs = {
            "FreeCAD": freecad,
            "FreeCADGui": gui,
            "PySide": types.SimpleNamespace(QtCore=qtcore),
            "PySide.QtCore": qtcore,
            "freecad.mcp_bridge.bridge": types.SimpleNamespace(),
        }
        with mock.patch.dict(sys.modules, stubs):
            sys.modules.pop("freecad.mcp_bridge.MCP_Bridge", None)
            importlib.import_module("freecad.mcp_bridge.MCP_Bridge")
            sys.modules.pop("freecad.mcp_bridge.init_gui", None)
            importlib.import_module("freecad.mcp_bridge.init_gui")
            sys.modules.pop("freecad.mcp_bridge.init_gui", None)
            sys.modules.pop("freecad.mcp_bridge.MCP_Bridge", None)
            self.assertEqual(len(recorded), 1)
            name, group, cmd = recorded[0]
            self.assertEqual(name, COMMAND_ID)
            self.assertEqual(group, "MCP_Bridge")
            self.assertEqual(
                cmd.GetResources()["MenuText"], "MCP Bridge On/Off"
            )


class ListenAddressAcceptanceTests(unittest.TestCase):
    def test_listen_addresses_and_loopback_detection(self):
        """ListenAddress splits on whitespace, commas, and semicolons (with
        colons marking ports), deduplicates in order, inherits an upgraded
        Port when ListenAddress is unset, and detects loopback by IP."""
        store = {}

        class DictParams:
            def GetString(self, name, default=""):
                return store.get(name, default)

            def SetString(self, name, value):
                store[name] = value

            def GetInt(self, name, default=0):
                return store.get(name, default)

            def RemInt(self, name):
                store.pop(name, None)

        freecad = types.SimpleNamespace(ParamGet=lambda _g: DictParams())
        with mock.patch.dict(sys.modules, {"FreeCAD": freecad}):
            sys.modules.pop("freecad.mcp_bridge.config", None)
            config = importlib.import_module("freecad.mcp_bridge.config")

            self.assertEqual(config.listen_addresses(), ["127.0.0.1:39280"])
            self.assertNotIn("ListenAddress", store)
            store["Port"] = 41234
            self.assertEqual(config.listen_addresses(), ["127.0.0.1:41234"])
            self.assertEqual(store.get("ListenAddress"), "127.0.0.1:41234")
            self.assertNotIn("Port", store)
            store["ListenAddress"] = (
                "127.0.0.1:39280\n192.168.1.50:39280, 10.0.0.5:39281; "
                "[::1]:39280 127.0.0.1:39280"
            )
            self.assertEqual(
                config.listen_addresses(),
                [
                    "127.0.0.1:39280",
                    "192.168.1.50:39280",
                    "10.0.0.5:39281",
                    "[::1]:39280",
                ],
            )
            store["Port"] = 41234
            store["ListenAddress"] = "   \n , ; "
            self.assertEqual(config.listen_addresses(), ["127.0.0.1:41234"])
            self.assertEqual(store.get("ListenAddress"), "127.0.0.1:41234")
            self.assertNotIn("Port", store)
            store.pop("ListenAddress", None)
            self.assertEqual(config.listen_addresses(), ["127.0.0.1:39280"])

            self.assertEqual(
                config.parse_listen_entry("127.0.0.1:39280"),
                ("127.0.0.1", 39280),
            )
            self.assertEqual(
                config.parse_listen_entry("[::1]:39280"), ("::1", 39280)
            )
            for bad in (
                "127.0.0.1",
                "[::1]",
                "::1:39280",
                "127.0.0.1:",
                ":39280",
                "127.0.0.1:0",
                "127.0.0.1:70000",
                "127.0.0.1:abc",
            ):
                with self.subTest(bad=bad):
                    with self.assertRaises(ValueError) as ctx:
                        config.parse_listen_entry(bad)
                    self.assertIn(bad, str(ctx.exception))

            self.assertTrue(config.is_loopback_address("127.0.0.1:39280"))
            self.assertTrue(config.is_loopback_address("127.0.0.2:39280"))
            self.assertTrue(config.is_loopback_address("[::1]:39280"))
            self.assertTrue(config.is_loopback_address("localhost:39280"))
            self.assertFalse(config.is_loopback_address("0.0.0.0:39280"))
            self.assertFalse(config.is_loopback_address("192.168.1.50:39280"))
            self.assertFalse(config.is_loopback_address("my-lan-host:39280"))
            for unspecified in ("0.0.0.0:39280", "[::]:39280", "[::0]:80"):
                with self.subTest(unspecified=unspecified):
                    with self.assertRaises(ValueError) as ctx:
                        config.parse_listen_entry(unspecified)
                    self.assertIn("specific address", str(ctx.exception))
                    self.assertIn(unspecified, str(ctx.exception))

    def test_per_address_acceptance_is_stored_and_queried_by_address(self):
        """Loopback is always accepted; non-loopback entries are stored as
        distinct items in AcceptedListenAddresses."""
        store = {}

        class DictParams:
            def GetString(self, name, default=""):
                return store.get(name, default)

            def SetString(self, name, value):
                store[name] = value

        freecad = types.SimpleNamespace(ParamGet=lambda _g: DictParams())
        with mock.patch.dict(sys.modules, {"FreeCAD": freecad}):
            sys.modules.pop("freecad.mcp_bridge.config", None)
            config = importlib.import_module("freecad.mcp_bridge.config")
            self.assertTrue(config.is_address_accepted("127.0.0.1:39280"))
            self.assertTrue(config.is_address_accepted("127.0.0.2:39280"))
            self.assertTrue(config.is_address_accepted("[::1]:39280"))
            self.assertTrue(config.is_address_accepted("localhost:39280"))
            self.assertFalse(config.is_address_accepted("192.168.1.50:39280"))
            self.assertFalse(config.is_address_accepted("0.0.0.0:39280"))

            config.accept_address("192.168.1.50:39280")
            self.assertTrue(config.is_address_accepted("192.168.1.50:39280"))
            self.assertFalse(config.is_address_accepted("192.168.1.50:39281"))
            self.assertFalse(config.is_address_accepted("0.0.0.0:39280"))
            self.assertEqual(
                config.accepted_listen_addresses(), ["192.168.1.50:39280"]
            )

            config.accept_address("0.0.0.0:39280")
            self.assertTrue(config.is_address_accepted("192.168.1.50:39280"))
            self.assertTrue(config.is_address_accepted("0.0.0.0:39280"))
            self.assertEqual(
                config.accepted_listen_addresses(),
                ["192.168.1.50:39280", "0.0.0.0:39280"],
            )

            # An item containing a comma is kept as one item, never split.
            config.accept_address("192.168.1.60:39280,192.168.1.61:39280")
            self.assertFalse(
                config.is_address_accepted("192.168.1.60:39280")
            )
            self.assertFalse(
                config.is_address_accepted("192.168.1.61:39280")
            )

    def test_bearer_token_is_written_to_the_file_and_reread_on_mismatch(self):
        """Generating a bearer token writes the token file, leaves TokenHash
        unread, and a mismatch reads the file again before refusing."""
        store = {"TokenHash": "left-unread", "RequireToken": False}

        class DictParams:
            def __init__(self):
                self.read = set()

            def GetString(self, name, default=""):
                self.read.add(name)
                return store.get(name, default)

            def SetString(self, name, value):
                store[name] = value

            def GetBool(self, name, default=False):
                self.read.add(name)
                return store.get(name, default)

        params = DictParams()
        freecad = types.SimpleNamespace(ParamGet=lambda _g: params)
        with mock.patch.dict(sys.modules, {"FreeCAD": freecad}):
            sys.modules.pop("freecad.mcp_bridge.config", None)
            config = importlib.import_module("freecad.mcp_bridge.config")
            with tempfile.TemporaryDirectory() as folder:
                home = Path(folder)
                with mock.patch.object(
                    config.token_file, "token_dir", return_value=home
                ):
                    self.assertEqual(config.token_hash(), "left-unread")
                    self.assertNotIn("RequireToken", params.read)
                    self.assertNotIn("RequireOnLoopback", params.read)
                    self.assertFalse(config.verify_token("anything"))

                    token1 = config.generate_and_store_token()
                    self.assertGreaterEqual(len(token1), 32)
                    self.assertEqual(
                        (home / "token").read_text(encoding="utf-8").strip(),
                        token1,
                    )
                    self.assertEqual(store.get("TokenHash"), "left-unread")
                    for val in store.values():
                        self.assertNotIn(token1, str(val))
                    self.assertTrue(config.verify_token(token1))
                    self.assertFalse(config.verify_token(token1 + "x"))
                    self.assertFalse(config.verify_token(""))

                    # A mismatch reads again, so a replacement is taken up.
                    real_read = config.token_file.read_token
                    reads = {"n": 0}

                    def flip_read():
                        reads["n"] += 1
                        if reads["n"] == 1:
                            return "stale-token-value-not-the-file"
                        return real_read()

                    with mock.patch.object(
                        config.token_file, "read_token", side_effect=flip_read
                    ):
                        self.assertTrue(config.verify_token(token1))
                    self.assertEqual(reads["n"], 2)

                    token2 = config.generate_and_store_token()
                    self.assertNotEqual(token1, token2)
                    self.assertFalse(config.verify_token(token1))
                    self.assertTrue(config.verify_token(token2))
                    for val in store.values():
                        self.assertNotIn(token2, str(val))
                    if os.name != "nt":
                        directory_mode = stat.S_IMODE(home.stat().st_mode)
                        file_mode = stat.S_IMODE(
                            (home / "token").stat().st_mode
                        )
                        self.assertEqual(directory_mode, 0o700)
                        self.assertEqual(file_mode, 0o600)

            # build_ssl_context passes an empty password callback so an
            # encrypted private key fails with ValueError instead of prompting.
            load_calls = []

            class FakeCtx:
                def load_cert_chain(
                    self, certfile=None, keyfile=None, password=None
                ):
                    load_calls.append(
                        (
                            certfile,
                            keyfile,
                            password() if callable(password) else password,
                        )
                    )
                    raise config.ssl.SSLError("bad password read")

            with mock.patch.object(
                config.ssl, "SSLContext", return_value=FakeCtx()
            ):
                with self.assertRaises(ValueError) as ssl_err:
                    config.build_ssl_context("cert.pem", "enc_key.pem")
            self.assertEqual(load_calls, [("cert.pem", "enc_key.pem", "")])
            self.assertIn(
                "Could not load TLS certificate or private key",
                str(ssl_err.exception),
            )

    def test_non_loopback_start_prompts_once_and_warns_each_start(self):
        """Turning on with unaccepted non-loopback entries shows only the
        remaining risks, fails when TLS is on with no certificate, shows a
        generated token once when RequireToken is on, uses a single preference
        snapshot, records acceptance only after bridge.start succeeds, and
        warns on every start."""
        store = {
            "ListenAddress": "127.0.0.1:39280\n192.168.1.50:39280",
            "RequireToken": False,
            "RequireTls": False,
        }
        running = False
        start_calls = []
        start_error = None
        prompts = []
        first_prompts = []
        shown_tokens = []
        statuses = []
        notifications = []
        warnings = []
        decision = False
        mutate_during_dialog = None

        class DictParams:
            def GetString(self, name, default=""):
                return store.get(name, default)

            def SetString(self, name, value):
                store[name] = value

            def GetInt(self, name, default=0):
                return store.get(name, default)

            def GetBool(self, name, default=False):
                return store.get(name, default)

            def SetBool(self, name, value):
                store[name] = value

        class FakeStatusBar:
            def showMessage(self, msg):
                statuses.append(msg)

            def currentMessage(self):
                return ""

        class FakeMainWindow:
            def statusBar(self):
                return FakeStatusBar()

        freecad = types.SimpleNamespace(
            ParamGet=lambda _g: DictParams(),
            Console=types.SimpleNamespace(
                PrintNotification=notifications.append,
                PrintWarning=warnings.append,
                PrintDeveloperError=lambda *_: None,
            ),
        )
        gui = types.SimpleNamespace(
            addIconPath=lambda *_: None,
            listCommands=lambda: [],
            addCommand=lambda *_: None,
            addPreferencePage=lambda *_: None,
            addWorkbenchManipulator=lambda *_: None,
            getMainWindow=lambda: FakeMainWindow(),
            Command=types.SimpleNamespace(get=lambda *_: None),
        )
        qtcore = types.SimpleNamespace(
            QTimer=types.SimpleNamespace(singleShot=lambda *_: None)
        )
        fake_bridge = types.SimpleNamespace(
            is_running=lambda: running,
            start=lambda **_: None,
            stop=lambda: None,
        )

        def start_bridge(addresses=None, **_kwargs):
            nonlocal running
            start_calls.append(list(addresses) if addresses else None)
            if start_error is not None:
                raise start_error
            running = True

        def stop_bridge():
            nonlocal running
            running = False

        fake_bridge.start = start_bridge
        fake_bridge.stop = stop_bridge

        stubs = {
            "FreeCAD": freecad,
            "FreeCADGui": gui,
            "PySide": types.SimpleNamespace(QtCore=qtcore),
            "PySide.QtCore": qtcore,
            "freecad.mcp_bridge.bridge": fake_bridge,
        }
        with mock.patch.dict(sys.modules, stubs):
            sys.modules.pop("freecad.mcp_bridge.config", None)
            config = importlib.import_module("freecad.mcp_bridge.config")
            sys.modules.pop("freecad.mcp_bridge.init_gui", None)
            init_gui = importlib.import_module("freecad.mcp_bridge.init_gui")
            init_gui.show_toggle_state = lambda: None
            token_root = tempfile.TemporaryDirectory()
            self.addCleanup(token_root.cleanup)
            token_patch = mock.patch.object(
                config.token_file,
                "token_dir",
                return_value=Path(token_root.name),
            )
            token_patch.start()
            self.addCleanup(token_patch.stop)

            def fake_confirm(
                entries, require_token=True, require_tls=True
            ):
                prompts.append(
                    (list(entries), bool(require_token), bool(require_tls))
                )
                if mutate_during_dialog is not None:
                    mutate_during_dialog()
                return decision

            def fake_first(
                entries, require_token=True, require_tls=True
            ):
                first_prompts.append(
                    (list(entries), bool(require_token), bool(require_tls))
                )
                return decision

            init_gui._confirm_non_loopback_risk = fake_confirm
            init_gui._confirm_first_start = fake_first
            init_gui._show_generated_token = (
                lambda tok, parent=None: shown_tokens.append(tok)
            )

            # Check risk dialog text across all four (require_token,
            # require_tls) combinations.
            title, body_none = init_gui._risk_dialog_text(
                ["192.168.1.50:39280", "10.0.0.5:39281"],
                require_token=False,
                require_tls=False,
            )
            self.assertEqual(
                title, "MCP Bridge — Listen beyond this machine?"
            )
            self.assertIn("192.168.1.50:39280\n10.0.0.5:39281", body_none)
            self.assertIn("No authentication", body_none)
            self.assertIn(
                "No encryption: requests and output travel over plain HTTP.",
                body_none,
            )
            self.assertIn(
                "Code execution: any machine that can reach these addresses",
                body_none,
            )
            self.assertIn(
                "Accept these risks and start the bridge?", body_none
            )

            _, body_tok_only = init_gui._risk_dialog_text(
                ["192.168.1.50:39280"],
                require_token=True,
                require_tls=False,
            )
            self.assertNotIn("No authentication", body_tok_only)
            self.assertIn(
                "No encryption: requests, output and the bearer token "
                "travel over plain HTTP.",
                body_tok_only,
            )
            self.assertIn(
                "any client with the bearer token", body_tok_only
            )

            _, body_tls_only = init_gui._risk_dialog_text(
                ["192.168.1.50:39280"],
                require_token=False,
                require_tls=True,
            )
            self.assertIn("No authentication", body_tls_only)
            self.assertNotIn("No encryption", body_tls_only)
            self.assertIn("any machine that can reach", body_tls_only)

            _, body_both = init_gui._risk_dialog_text(
                ["192.168.1.50:39280"],
                require_token=True,
                require_tls=True,
            )
            self.assertNotIn("No authentication", body_both)
            self.assertNotIn("No encryption", body_both)
            self.assertIn("any client with the bearer token", body_both)

            tok_title, tok_body = init_gui._token_dialog_text("tok-sample")
            self.assertEqual(tok_title, "MCP Bridge — Bearer token")
            self.assertIn("readable only by you", tok_body)
            self.assertIn(str(config.token_file.token_path()), tok_body)
            self.assertIn(
                "The Claude Desktop extension reads it from that file.",
                tok_body,
            )
            self.assertIn(
                "Other clients send it in the Authorization header:",
                tok_body,
            )
            self.assertNotIn("one-way hash", tok_body)
            self.assertIn("Authorization: Bearer tok-sample", tok_body)
            self.assertIn(
                "Every other client then needs the new token.", tok_body
            )
            start_title, start_body = init_gui._first_start_dialog_text([])
            self.assertEqual(start_title, "MCP Bridge — Start the bridge?")
            self.assertIn("full permissions", start_body)
            self.assertIn("only after you accept", start_body)
            _both_title, both_body = init_gui._first_start_dialog_text(
                ["192.168.1.50:39280"],
                require_token=True,
                require_tls=True,
            )
            self.assertIn("full permissions", both_body)
            self.assertIn("192.168.1.50:39280", both_body)
            self.assertIn(
                "Accept these risks and start the bridge?", both_body
            )

            cmd = init_gui.MCPBridgeCommand()

            # 0a. Entry without a port fails before any dialog, names the
            # entry, and leaves the bridge off.
            store["ListenAddress"] = "127.0.0.1:39280\n192.168.1.50"
            cmd.Activated()
            self.assertFalse(running)
            self.assertEqual(prompts, [])
            self.assertIn("192.168.1.50", statuses[-1])
            self.assertIn("Change the listen addresses in Edit", warnings[-1])

            # 0b. With RequireTls=True and no certificate/key set, a
            # non-loopback start fails before any dialog and stays off.
            store["ListenAddress"] = "127.0.0.1:39280\n192.168.1.50:39280"
            store["RequireTls"] = True
            cmd.Activated()
            self.assertFalse(running)
            self.assertEqual(prompts, [])
            self.assertEqual(shown_tokens, [])
            self.assertIn(
                "TLS is required and needs a certificate file and a private "
                "key file.",
                statuses[-1],
            )
            self.assertIn("untick Require TLS there.", warnings[-1])
            self.assertNotIn(
                "Change the listen addresses in Edit", warnings[-1]
            )
            store["RequireTls"] = False

            # 0c. A first start that also has an unaccepted address
            # beyond loopback uses one dialog, and declining saves neither
            # acceptance. The old Apply-to-loopback preference does not
            # make a loopback-only start demand a certificate.
            store["ListenAddress"] = "127.0.0.1:39280\n192.168.1.50:39280"
            store["RequireTls"] = False
            decision = False
            cmd.Activated()
            self.assertFalse(running)
            self.assertEqual(prompts, [])
            self.assertEqual(
                first_prompts, [(["192.168.1.50:39280"], True, False)]
            )
            self.assertEqual(config.accepted_listen_addresses(), [])
            self.assertFalse(config.start_warning_accepted())
            store["ListenAddress"] = "127.0.0.1:39280"
            store["RequireOnLoopback"] = True
            store["RequireTls"] = True
            cmd.Activated()
            self.assertFalse(running)
            self.assertEqual(prompts, [])
            self.assertEqual(first_prompts[-1], ([], True, True))
            self.assertEqual(shown_tokens, [])
            self.assertEqual(statuses[-1], "MCP Bridge: Offline")
            self.assertFalse(config.start_warning_accepted())
            decision = True
            cmd.Activated()
            self.assertTrue(running)
            self.assertEqual(prompts, [])
            self.assertEqual(len(shown_tokens), 1)
            self.assertTrue(config.verify_token(shown_tokens[0]))
            self.assertTrue(config.start_warning_accepted())
            self.assertEqual(
                statuses[-1],
                "MCP Bridge: Listening on 127.0.0.1:39280 "
                "(warning: connected agents can run code with your "
                "full permissions)",
            )
            self.assertIn("full permissions", notifications[-1])
            cmd.Activated()
            self.assertFalse(running)
            shown_before = len(shown_tokens)
            first_before = len(first_prompts)
            cmd.Activated()
            self.assertTrue(running)
            self.assertEqual(len(shown_tokens), shown_before)
            self.assertEqual(len(first_prompts), first_before)
            self.assertIn("full permissions", statuses[-1])

            # 1. Decline a new non-loopback entry: bridge stays off.
            store["RequireTls"] = False
            store["RequireOnLoopback"] = False
            store["ListenAddress"] = "127.0.0.1:39280\n192.168.1.50:39280"
            decision = False
            cmd.Activated()
            self.assertFalse(running)
            cmd.Activated()
            self.assertFalse(running)
            self.assertEqual(
                prompts, [(["192.168.1.50:39280"], True, False)]
            )
            self.assertEqual(statuses[-1], "MCP Bridge: Offline")
            self.assertEqual(config.accepted_listen_addresses(), [])

            # 2. Accept when bridge.start fails: acceptance is NOT saved.
            decision = True
            start_error = OSError("address not available")
            cmd.Activated()
            self.assertFalse(running)
            self.assertEqual(config.accepted_listen_addresses(), [])
            start_error = None

            # 3. Accept when Preferences changes mid-dialog: snapshot is used.
            def mutate_prefs():
                store["ListenAddress"] = "10.0.0.99:41000"

            mutate_during_dialog = mutate_prefs
            cmd.Activated()
            mutate_during_dialog = None
            self.assertTrue(running)
            self.assertEqual(
                start_calls[-1], ["127.0.0.1:39280", "192.168.1.50:39280"]
            )
            self.assertEqual(
                config.accepted_listen_addresses(), ["192.168.1.50:39280"]
            )
            self.assertEqual(
                statuses[-1],
                "MCP Bridge: Listening on 127.0.0.1:39280, "
                "192.168.1.50:39280 (warning: connected agents can "
                "run code with your full permissions; beyond "
                "loopback: 192.168.1.50:39280)",
            )

            # 4. Stop and start again at the accepted entry: no prompt.
            store["ListenAddress"] = "127.0.0.1:39280\n192.168.1.50:39280"
            cmd.Activated()
            self.assertFalse(running)
            prompt_count = len(prompts)
            cmd.Activated()
            self.assertTrue(running)
            self.assertEqual(len(prompts), prompt_count)
            self.assertIn("full permissions", statuses[-1])
            self.assertIn("beyond loopback", statuses[-1])

            # 5. An empty token file on a later start shows a new token once.
            token_file = Path(token_root.name) / "token"
            token_file.unlink()
            cmd.Activated()
            self.assertFalse(running)
            cmd.Activated()
            self.assertTrue(running)
            self.assertEqual(len(shown_tokens), shown_before + 1)
            self.assertTrue(config.verify_token(shown_tokens[-1]))

            # 6. Add two new specific addresses: one dialog lists both.
            # An unspecified address is refused before any dialog.
            cmd.Activated()
            store["ListenAddress"] = (
                "127.0.0.1:39280\n192.168.1.50:39280\n"
                "10.0.0.5:39281\n10.0.0.9:39282"
            )
            cmd.Activated()
            self.assertTrue(running)
            self.assertEqual(
                prompts[-1],
                (["10.0.0.5:39281", "10.0.0.9:39282"], True, False),
            )
            self.assertEqual(
                config.accepted_listen_addresses(),
                [
                    "192.168.1.50:39280",
                    "10.0.0.5:39281",
                    "10.0.0.9:39282",
                ],
            )
            cmd.Activated()
            prompt_count = len(prompts)
            store["ListenAddress"] = "0.0.0.0:39280"
            cmd.Activated()
            self.assertFalse(running)
            self.assertEqual(len(prompts), prompt_count)
            self.assertIn("specific address", statuses[-1])
            sys.modules.pop("freecad.mcp_bridge.init_gui", None)

    def test_http_server_binds_configured_list_and_rolls_back_on_failure(
        self,
    ):
        """HttpServer binds the exact configured address:port list, enforces
        bearer token and TLS settings on non-loopback entries only, warns when
        any entry is non-loopback, and rolls back cleanly on bind or thread
        start failure without shutting down an unstarted server."""
        store = {
            "ListenAddress": "127.0.0.1:39280",
            "RequireToken": False,
            "RequireTls": False,
        }
        messages = []
        warnings = []
        errors = []
        bound = []
        wrapped_sockets = []
        shut_down = []
        closed = []
        fail_bind_on = None
        fail_wrap_on = False
        fail_thread_on = None

        class DictParams:
            def GetString(self, name, default=""):
                return store.get(name, default)

            def SetString(self, name, value):
                store[name] = value

            def GetInt(self, name, default=0):
                return store.get(name, default)

            def GetBool(self, name, default=False):
                return store.get(name, default)

        class FakeHTTPServer:
            def __init__(self, addr, _handler):
                if fail_bind_on is not None and addr[0] == fail_bind_on:
                    raise OSError(f"cannot bind {addr[0]}")
                self.addr = addr
                self.socket = f"raw-sock-{addr[0]}:{addr[1]}"
                self.executor = None
                self.require_token = False
                bound.append(addr)

            def serve_forever(self):
                pass

            def shutdown(self):
                shut_down.append(self.addr)

            def server_close(self):
                closed.append(self.addr)

        class FakeThread:
            def __init__(self, target=None, name=None, daemon=None):
                self._name = name
                self._alive = False

            def start(self):
                if fail_thread_on is not None and self._name == fail_thread_on:
                    raise RuntimeError("thread start failed")
                self._alive = True

            def is_alive(self):
                return self._alive

        class FakeSSLContext:
            def wrap_socket(
                self,
                sock,
                server_side=False,
                do_handshake_on_connect=True,
            ):
                if fail_wrap_on:
                    raise OSError("tls wrap failed")
                wrapped = (
                    f"tls({sock}, server_side={server_side}, "
                    f"do_handshake_on_connect={do_handshake_on_connect})"
                )
                wrapped_sockets.append(wrapped)
                return wrapped

        freecad = types.SimpleNamespace(
            ParamGet=lambda _g: DictParams(),
            Console=types.SimpleNamespace(
                PrintMessage=messages.append,
                PrintWarning=warnings.append,
                PrintDeveloperError=errors.append,
            ),
        )
        executor_shutdowns = []
        fake_executor_mod = types.SimpleNamespace(
            Executor=lambda: types.SimpleNamespace(
                shutdown=lambda: executor_shutdowns.append(True)
            )
        )
        stubs = {
            "FreeCAD": freecad,
            "freecad.mcp_bridge.executor": fake_executor_mod,
        }
        with mock.patch.dict(sys.modules, stubs):
            sys.modules.pop("freecad.mcp_bridge.config", None)
            config = importlib.import_module("freecad.mcp_bridge.config")
            sys.modules.pop("freecad.mcp_bridge.http_server", None)
            http_server = importlib.import_module(
                "freecad.mcp_bridge.http_server"
            )
            with (
                mock.patch.object(
                    http_server.paging,
                    "MaintenanceHTTPServer",
                    FakeHTTPServer,
                ),
                mock.patch.object(
                    http_server.threading, "Thread", FakeThread
                ),
            ):
                srv = http_server.HttpServer()
                token_root = tempfile.TemporaryDirectory()
                self.addCleanup(token_root.cleanup)
                token_patch = mock.patch.object(
                    config.token_file,
                    "token_dir",
                    return_value=Path(token_root.name),
                )
                token_patch.start()
                self.addCleanup(token_patch.stop)

                # 1. Default loopback: binds 127.0.0.1:39280 only, no warning.
                srv.start()
                self.assertEqual(bound, [("127.0.0.1", 39280)])
                self.assertEqual(warnings, [])
                self.assertTrue(srv._servers[0][0].require_token)
                self.assertEqual(
                    srv._servers[0][0].bound_host, "127.0.0.1"
                )
                srv.stop()

                # 2. List with loopback, LAN, and IPv6 on distinct ports.
                bound.clear()
                shut_down.clear()
                closed.clear()
                store["ListenAddress"] = (
                    "127.0.0.1:39280\n192.168.1.50:39281\n[::1]:39282"
                )
                srv.start()
                self.assertEqual(
                    bound,
                    [
                        ("127.0.0.1", 39280),
                        ("192.168.1.50", 39281),
                        ("::1", 39282),
                    ],
                )
                self.assertEqual(len(warnings), 1)
                self.assertEqual(
                    warnings[0],
                    "[MCP Bridge] WARNING: listening on 127.0.0.1:39280, "
                    "192.168.1.50:39281, [::1]:39282 with no encryption. "
                    "Any client with the bearer token that can reach "
                    "these addresses can run Python code in FreeCAD as "
                    "your user account: 192.168.1.50:39281.\n",
                )
                self.assertTrue(srv._servers[0][0].require_token)
                self.assertTrue(srv._servers[1][0].require_token)
                self.assertTrue(srv._servers[2][0].require_token)
                srv.stop()
                self.assertEqual(
                    shut_down,
                    [
                        ("::1", 39282),
                        ("192.168.1.50", 39281),
                        ("127.0.0.1", 39280),
                    ],
                )
                self.assertEqual(
                    closed,
                    [
                        ("::1", 39282),
                        ("192.168.1.50", 39281),
                        ("127.0.0.1", 39280),
                    ],
                )

                # 2b. With RequireTls=True: missing cert raises ValueError and
                # stays off; a token file is not required to bind; failed
                # wrap_socket closes the bound socket; a valid cert wraps
                # only the non-loopback socket with
                # do_handshake_on_connect=False. Every listener requires
                # the token.
                bound.clear()
                closed.clear()
                warnings.clear()
                errors.clear()
                store["RequireToken"] = True
                store["RequireTls"] = True
                with self.assertRaises(ValueError):
                    srv.start()
                self.assertFalse(srv.is_running())
                self.assertEqual(bound, [])
                self.assertIn(
                    "TLS is required and needs a certificate file and a "
                    "private key file.",
                    errors[-1],
                )
                with mock.patch.object(
                    config,
                    "build_ssl_context",
                    return_value=FakeSSLContext(),
                ):
                    fail_wrap_on = True
                    with self.assertRaises(OSError):
                        srv.start(
                            tls_cert_file="cert.pem", tls_key_file="key.pem"
                        )
                    fail_wrap_on = False
                    self.assertFalse(srv.is_running())
                    self.assertEqual(
                        closed,
                        [("127.0.0.1", 39280), ("192.168.1.50", 39281)],
                    )
                    bound.clear()
                    closed.clear()

                    srv.start(
                        tls_cert_file="cert.pem", tls_key_file="key.pem"
                    )
                self.assertTrue(srv.is_running())
                self.assertTrue(srv._servers[0][0].require_token)
                self.assertTrue(srv._servers[1][0].require_token)
                self.assertTrue(srv._servers[2][0].require_token)
                self.assertEqual(
                    wrapped_sockets,
                    [
                        "tls(raw-sock-192.168.1.50:39281, "
                        "server_side=True, do_handshake_on_connect=False)"
                    ],
                )
                self.assertEqual(
                    warnings[-1],
                    "[MCP Bridge] WARNING: listening on 127.0.0.1:39280, "
                    "192.168.1.50:39281, [::1]:39282. TLS is required "
                    "beyond loopback, and the bearer token everywhere. "
                    "Any client with the "
                    "bearer token that can reach these addresses can run "
                    "Python code in FreeCAD as your user account: "
                    "192.168.1.50:39281.\n",
                )
                srv.stop()

                # 2c. The old Apply-to-loopback preference does not wrap
                # loopback in TLS, and there is no non-loopback warning.
                bound.clear()
                closed.clear()
                warnings.clear()
                errors.clear()
                wrapped_sockets.clear()
                store["ListenAddress"] = "127.0.0.1:39280"
                store["RequireOnLoopback"] = True
                store["RequireTls"] = True
                srv.start()
                self.assertTrue(srv.is_running())
                self.assertTrue(srv._servers[0][0].require_token)
                self.assertEqual(wrapped_sockets, [])
                self.assertEqual(warnings, [])
                srv.stop()
                store["RequireOnLoopback"] = False
                store["RequireToken"] = False
                store["RequireTls"] = False

                # 2d. An unspecified address is refused before any bind.
                bound.clear()
                errors.clear()
                for entry in ("0.0.0.0:39280", "[::]:39280"):
                    store["ListenAddress"] = entry
                    with self.assertRaises(ValueError) as unspecified:
                        srv.start()
                    self.assertIn(
                        "specific address", str(unspecified.exception)
                    )
                    self.assertEqual(bound, [])
                    self.assertFalse(srv.is_running())

                # 3. LAN only (127.0.0.1 removed): binds LAN only, warns.
                bound.clear()
                warnings.clear()
                store["ListenAddress"] = "192.168.1.50:39280"
                srv.start()
                self.assertEqual(bound, [("192.168.1.50", 39280)])
                self.assertEqual(len(warnings), 1)
                srv.stop()

                # 4. Entry without port: raises ValueError before binding.
                bound.clear()
                errors.clear()
                store["ListenAddress"] = "127.0.0.1:39280\n192.168.1.50"
                with self.assertRaises(ValueError):
                    srv.start()
                self.assertFalse(srv.is_running())
                self.assertEqual(bound, [])
                self.assertIn("192.168.1.50", errors[-1])

                # 5. Bind failure on 2nd address: closes 1st, stays off.
                bound.clear()
                shut_down.clear()
                closed.clear()
                errors.clear()
                fail_bind_on = "192.168.1.50"
                store["ListenAddress"] = (
                    "127.0.0.1:39280\n192.168.1.50:39280"
                )
                with self.assertRaises(OSError):
                    srv.start()
                fail_bind_on = None
                self.assertFalse(srv.is_running())
                self.assertEqual(shut_down, [])
                self.assertEqual(closed, [("127.0.0.1", 39280)])
                self.assertIn(
                    "Could not bind 192.168.1.50:39280", errors[-1]
                )

                # 6. Thread start failure on 2nd server: shuts down 1st,
                # closes both, never calls shutdown() on unstarted 2nd.
                bound.clear()
                shut_down.clear()
                closed.clear()
                fail_thread_on = "mcp-bridge-http-1"
                with self.assertRaises(RuntimeError):
                    srv.start()
                fail_thread_on = None
                self.assertFalse(srv.is_running())
                self.assertEqual(shut_down, [("127.0.0.1", 39280)])
                self.assertEqual(
                    closed,
                    [("127.0.0.1", 39280), ("192.168.1.50", 39280)],
                )
                # Subsequent stop() is a clean no-op.
                shut_down.clear()
                closed.clear()
                srv.stop()
                self.assertEqual(shut_down, [])
                self.assertEqual(closed, [])

                # 7. Dead serving thread: is_running() is False, and the next
                # start() cleans up held servers and executor before binding
                # (even when the new start fails to bind).
                bound.clear()
                shut_down.clear()
                closed.clear()
                executor_shutdowns.clear()
                store["ListenAddress"] = (
                    "127.0.0.1:39280\n192.168.1.50:39281"
                )
                srv.start()
                self.assertTrue(srv.is_running())
                srv._servers[1][1]._alive = False
                self.assertFalse(srv.is_running())
                bound.clear()
                shut_down.clear()
                closed.clear()
                executor_shutdowns.clear()
                fail_bind_on = "127.0.0.1"
                with self.assertRaises(OSError):
                    srv.start()
                fail_bind_on = None
                self.assertEqual(shut_down, [("127.0.0.1", 39280)])
                self.assertEqual(
                    closed,
                    [("192.168.1.50", 39281), ("127.0.0.1", 39280)],
                )
                self.assertEqual(len(executor_shutdowns), 2)
                self.assertEqual(srv._servers, [])
                self.assertIsNone(srv._executor)

                # 8. Every listener checks the bearer token. A replaced
                # file is taken up without a restart. Any Origin is
                # refused. Host must be the bound address, with the
                # loopback aliases on a loopback listener.
                tok = config.generate_and_store_token()
                handler = http_server.McpRequestHandler.__new__(
                    http_server.McpRequestHandler
                )
                handler.server = types.SimpleNamespace(
                    require_token=False, bound_host="127.0.0.1"
                )
                handler.headers = {}
                self.assertFalse(handler._token_ok())
                self.assertTrue(handler._origin_ok())
                self.assertFalse(handler._host_ok())

                handler.headers = {"Authorization": f"Basic {tok}"}
                self.assertFalse(handler._token_ok())
                handler.headers = {"Authorization": "Bearer wrong-token"}
                self.assertFalse(handler._token_ok())
                handler.headers = {"Authorization": f"Bearer {tok}"}
                self.assertTrue(handler._token_ok())

                tok2 = config.generate_and_store_token()
                self.assertFalse(handler._token_ok())
                handler.headers = {"Authorization": f"Bearer {tok2}"}
                self.assertTrue(handler._token_ok())

                for host in ("127.0.0.1:39280", "localhost", "[::1]:39280"):
                    handler.headers = {"Host": host}
                    self.assertTrue(handler._host_ok(), host)
                handler.headers = {"Host": "192.168.1.50"}
                self.assertFalse(handler._host_ok())
                handler.server.bound_host = "192.168.1.50"
                handler.headers = {"Host": "192.168.1.50:39281"}
                self.assertTrue(handler._host_ok())
                handler.headers = {"Host": "localhost"}
                self.assertFalse(handler._host_ok())
                handler.headers = {"Host": "[2001:db8::5]:443"}
                self.assertFalse(handler._host_ok())
                handler.server.bound_host = "2001:db8::5"
                handler.headers = {"Host": "[2001:db8::5]:443"}
                self.assertTrue(handler._host_ok())

                handler.headers = {"Origin": ""}
                self.assertFalse(handler._origin_ok())
                handler.headers = {"Origin": "http://127.0.0.1"}
                self.assertFalse(handler._origin_ok())
                handler.server.bound_host = "127.0.0.1"

                # 9. A rejected POST sends 401 with WWW-Authenticate: Bearer,
                # and handle() runs the TLS handshake with a timeout.
                sent_status = []
                sent_headers = []
                written = []
                refused = []
                handler.send_error = (
                    lambda code, message=None, explain=None: refused.append(
                        (code, message)
                    )
                )
                handler.headers = {
                    "Origin": "http://127.0.0.1",
                    "Host": "127.0.0.1:39280",
                    "Authorization": f"Bearer {tok2}",
                }
                handler.do_POST()
                self.assertEqual(refused, [(403, "Forbidden Origin")])
                handler.headers = {"Host": "evil.example"}
                handler.do_POST()
                self.assertEqual(refused[-1], (403, "Forbidden Host"))

                handler.headers = {"Host": "127.0.0.1:39280"}
                handler.send_response = lambda code, msg=None: (
                    sent_status.append((code, msg))
                )
                handler.send_header = lambda k, v: sent_headers.append((k, v))
                handler.end_headers = lambda: None
                handler.wfile = types.SimpleNamespace(write=written.append)
                handler.do_POST()
                self.assertEqual(sent_status, [(401, "Unauthorized")])
                self.assertIn(("WWW-Authenticate", "Bearer"), sent_headers)
                self.assertEqual(written, [b"Unauthorized\n"])

                timeouts = []
                handshakes = []

                class FakeTLSRequest:
                    def __init__(self, fail=False):
                        self._fail = fail

                    def settimeout(self, val):
                        timeouts.append(val)

                    def do_handshake(self):
                        handshakes.append(True)
                        if self._fail:
                            raise OSError("handshake timeout")

                handler.request = FakeTLSRequest(fail=True)
                handler.handle()
                self.assertEqual(
                    timeouts, [http_server.TLS_HANDSHAKE_TIMEOUT_S]
                )
                self.assertEqual(len(handshakes), 1)
            sys.modules.pop("freecad.mcp_bridge.http_server", None)


def _windows_ace_count(path):
    """How many allow entries the file's DACL has, on Windows."""
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    se_file_object = 1
    dacl_security_information = 0x4
    acl_size_information = 2

    class AclSizeInformation(ctypes.Structure):
        _fields_ = [
            ("AceCount", wintypes.DWORD),
            ("AclBytesInUse", wintypes.DWORD),
            ("AclBytesFree", wintypes.DWORD),
        ]

    advapi32.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi32.GetAclInformation.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    advapi32.GetAclInformation.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    owner = ctypes.c_void_p()
    group = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    sacl = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    result = advapi32.GetNamedSecurityInfoW(
        str(path),
        se_file_object,
        dacl_security_information,
        ctypes.byref(owner),
        ctypes.byref(group),
        ctypes.byref(dacl),
        ctypes.byref(sacl),
        ctypes.byref(descriptor),
    )
    if result != 0:
        raise ctypes.WinError(result)
    try:
        info = AclSizeInformation()
        if not advapi32.GetAclInformation(
            dacl,
            ctypes.byref(info),
            ctypes.sizeof(info),
            acl_size_information,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return info.AceCount
    finally:
        kernel32.LocalFree(descriptor)


class TokenFileTests(unittest.TestCase):
    def test_freecad_mcp_token_dir_overrides_the_home_folder(self):
        """FREECAD_MCP_TOKEN_DIR is the token folder when it is set."""
        from freecad.mcp_bridge import token_file

        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.dict(
                os.environ, {"FREECAD_MCP_TOKEN_DIR": folder}
            ):
                self.assertEqual(token_file.token_dir(), Path(folder))
                token_file.write_token("a" * 32)
            self.assertEqual(
                (Path(folder) / "token").read_text(encoding="utf-8").strip(),
                "a" * 32,
            )
            leftovers = [
                name
                for name in os.listdir(folder)
                if name.endswith(".tmp")
            ]
            self.assertEqual(leftovers, [])
            if os.name == "nt":
                self.assertEqual(
                    _windows_ace_count(Path(folder) / "token"), 1
                )
            else:
                mode = stat.S_IMODE((Path(folder) / "token").stat().st_mode)
                self.assertEqual(mode, 0o600)

    def test_the_acceptance_client_reads_the_same_override(self):
        """mcp_client reads FREECAD_MCP_TOKEN_DIR, and the home folder otherwise."""
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "token").write_text(
                "b" * 32 + "\n", encoding="utf-8"
            )
            with mock.patch.dict(
                os.environ,
                {
                    "BRIDGE_URL": "http://127.0.0.1:9/mcp",
                    "FREECAD_MCP_TOKEN_DIR": folder,
                },
            ):
                sys.modules.pop("tests.acceptance.mcp_client", None)
                client = importlib.import_module("tests.acceptance.mcp_client")
                self.assertEqual(client._token_from_file(), "b" * 32)
                self.assertEqual(client._token_dir(), Path(folder))
            sys.modules.pop("tests.acceptance.mcp_client", None)

    def test_generate_new_token_reports_a_failed_write(self):
        """Generate new token shows the write error and does not show a token."""
        init_gui = _fresh_init_gui()
        shown = []
        init_gui._show_token_write_failure = (
            lambda exc, parent=None: shown.append(str(exc))
        )
        init_gui._show_generated_token = (
            lambda *args, **kwargs: shown.append("shown")
        )
        text = init_gui._token_write_failure_text(OSError("access denied"))
        self.assertIn("Could not write the bearer token file", text)
        self.assertIn("access denied", text)
        self.assertIn(
            "Check that the folder is writable, then try again.", text
        )
        with mock.patch(
            "freecad.mcp_bridge.config.generate_and_store_token",
            side_effect=OSError("access denied"),
        ):
            self.assertIsNone(init_gui.generate_and_show_token())
        self.assertEqual(shown, ["access denied"])

    def test_a_windows_sharing_error_retries_the_replace(self):
        """A sharing error on Windows retries the replace, then the token is whole."""
        if os.name != "nt":
            self.skipTest("the retry is for a Windows sharing error")
        from freecad.mcp_bridge import token_file

        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.dict(
                os.environ, {"FREECAD_MCP_TOKEN_DIR": folder}
            ):
                real = os.replace
                calls = {"n": 0}

                def flaky(source, dest):
                    calls["n"] += 1
                    if calls["n"] < 3:
                        error = PermissionError(13, "sharing", source, 32)
                        error.winerror = 32
                        raise error
                    return real(source, dest)

                with mock.patch("os.replace", side_effect=flaky):
                    token_file.write_token("c" * 32)
            self.assertGreaterEqual(calls["n"], 3)
            self.assertEqual(
                (Path(folder) / "token").read_text(encoding="utf-8").strip(),
                "c" * 32,
            )


class PermissionModeTests(unittest.TestCase):
    def test_the_acceptance_client_puts_ask_back_only_where_it_found_ask(
        self,
    ):
        """The client goes Unrestricted once, before its first run. It
        registers the return to Ask only when Ask is what it found."""
        for found, registered in (("False", 1), ("True", 0)):
            with mock.patch.dict(
                os.environ, {"BRIDGE_URL": "http://127.0.0.1:9/mcp"}
            ):
                sys.modules.pop("tests.acceptance.mcp_client", None)
                client = importlib.import_module(
                    "tests.acceptance.mcp_client"
                )
            sys.modules.pop("tests.acceptance.mcp_client", None)
            posted = []
            page = [
                {"stream": "stdout", "text": f"mode before: {found}\n"}
            ]
            result = {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "has_more": False,
                                "page_no": 0,
                                "job_token": "t",
                                "page": page,
                            }
                        ),
                    }
                ]
            }
            with (
                mock.patch.object(
                    client,
                    "post",
                    side_effect=lambda message, **_kw: posted.append(
                        str(message)
                    ),
                ),
                mock.patch.object(
                    client, "tool_result", return_value=result
                ),
                mock.patch.object(client.atexit, "register") as register,
            ):
                client.call_tool("execute_python", {"code": "print(1)"})
                client.call_tool("execute_python", {"code": "print(2)"})
                self.assertEqual(len(posted), 3, found)
                self.assertIn("set_unrestricted(True)", posted[0])
                self.assertEqual(register.call_count, registered, found)
                if registered:
                    register.assert_called_once_with(client._back_to_ask)
                    client._back_to_ask()
                    self.assertIn("set_unrestricted(False)", posted[-1])

    def test_choosing_the_mode_in_force_asks_nothing(self):
        """The Preferences box acts only on a change of mode."""
        init_gui = _fresh_init_gui()
        applied = []
        init_gui._apply_mode_choice = (
            lambda want, box: applied.append((want, box))
        )
        with mock.patch(
            "freecad.mcp_bridge.config.unrestricted", return_value=False
        ):
            init_gui._on_mode_chosen("box", 0)
            self.assertEqual(applied, [])
            init_gui._on_mode_chosen("box", 1)
            self.assertEqual(applied, [(True, "box")])
        with mock.patch(
            "freecad.mcp_bridge.config.unrestricted", return_value=True
        ):
            init_gui._on_mode_chosen("box", 1)
            self.assertEqual(applied, [(True, "box")])
            init_gui._on_mode_chosen("box", 0)
            self.assertEqual(applied, [(True, "box"), (False, "box")])

    def test_the_marker_asks_to_go_unrestricted_and_not_to_go_back(self):
        """A click on the marker asks once to go Unrestricted, and only
        Yes switches. A click back to Ask is at once. Each click
        refreshes the marker and an open Preferences box."""
        init_gui = _fresh_init_gui()
        state = {"unrestricted": False}
        answers = ["No", "Yes"]
        asked = []

        def ask(parent=None):
            asked.append(parent)
            return answers.pop(0)

        with (
            mock.patch(
                "freecad.mcp_bridge.config.unrestricted",
                side_effect=lambda: state["unrestricted"],
            ),
            mock.patch(
                "freecad.mcp_bridge.config.set_unrestricted",
                side_effect=lambda on: state.update(unrestricted=on),
            ),
            mock.patch(
                "freecad.mcp_bridge.file_prompt._ask_unrestricted",
                side_effect=ask,
            ),
            mock.patch.object(init_gui, "refresh_mode_marker") as marker,
            mock.patch.object(init_gui, "_sync_open_mode_box") as box,
        ):
            init_gui._on_marker_clicked()
            self.assertIs(state["unrestricted"], False)
            init_gui._on_marker_clicked()
            self.assertIs(state["unrestricted"], True)
            init_gui._on_marker_clicked()
            self.assertIs(state["unrestricted"], False)
            self.assertEqual(asked, [None, None])
            self.assertEqual(marker.call_count, 3)
            self.assertEqual(box.call_count, 3)


if __name__ == "__main__":
    unittest.main()
