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
from freecad.mcp_bridge.file_prompt import (
    MENU_ASK,
    MENU_PREFERENCES,
    MENU_UNRESTRICTED,
    MODE_TOOLTIP,
    status_button_shown,
)

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "freecad" / "mcp_bridge" / "Resources" / "Ui" / "preferences.ui"
# Where a preference widget's prefPath is rooted in FreeCAD's parameters.
PREF_ROOT = "User parameter:BaseApp/Preferences"
SETTINGS = (
    "ListenAddress",
    "TlsCertFile",
    "TlsKeyFile",
    "MaxResponseTimeoutSeconds",
    "MaxPageSize",
    "MaxHistoryRetentionMinutes",
    "ShowStatusButton",
    "StatusButtonPosition",
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
        self.assertNotIn("RequireTls", settings)
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
        mode_box = next(
            widget
            for widget in ET.parse(PAGE).iter("widget")
            if widget.get("name") == "permissionModeComboBox"
        )
        mode_props = {
            prop.get("name"): prop[0].text
            for prop in mode_box.findall("property")
        }
        self.assertEqual(
            [
                item.find("property/string").text
                for item in mode_box.findall("item")
            ],
            ["Ask (default)", "Unrestricted"],
        )
        self.assertEqual(mode_props["currentIndex"], "0")
        self.assertEqual(mode_props["toolTip"], MODE_TOOLTIP)
        self.assertEqual(
            MODE_TOOLTIP,
            "Ask shows you an agent's code first. Unrestricted runs "
            "it unseen.",
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
        self.assertEqual(
            widgets_by_name["listenAddressNote"]["text"],
            "Beyond loopback, the bridge serves HTTPS only, and an MCP "
            "client must trust the certificate in TLS certificate file "
            "(PEM) below.",
        )
        self.assertIn(
            "Beyond loopback, use <br/><nobr>https:&#x2060;//&#x2060;"
            "&lt;ADDRESS&gt;&#x2060;:&#x2060;&lt;PORT&gt;/mcp</nobr>. "
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
        self.assertEqual(config.tls_cert_file(), "")
        self.assertEqual(config.tls_key_file(), "")
        self.assertEqual(config.max_response_timeout_ms(), 15_000)
        self.assertEqual(config.max_page_size_chars(), 32_768)
        self.assertEqual(config.max_history_retention_ms(), 5 * 60_000)
        self.assertIs(config.show_status_button(), True)
        self.assertEqual(config.status_button_position(), 0)
        self.assertIs(config.unrestricted(), False)
        # What it read is what the preference page saves, plus Port as the
        # upgrade fallback when ListenAddress has never been saved, and
        # PermissionMode, which only a confirmed choice writes.
        self.assertEqual(set(groups), {PREF_GROUP})
        self.assertEqual(
            params.read, set(SETTINGS) | {"Port", "PermissionMode"}
        )


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
        remaining risk, fails when TLS files are missing or the certificate
        does not cover the address, shows a generated token once, uses a
        single preference snapshot, records acceptance only after bridge.start
        succeeds, and warns on every start."""
        store = {
            "ListenAddress": "127.0.0.1:39280\n192.168.1.50:39280",
            "TlsCertFile": "cert.pem",
            "TlsKeyFile": "key.pem",
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

            def fake_confirm(entries):
                prompts.append(list(entries))
                if mutate_during_dialog is not None:
                    mutate_during_dialog()
                return decision

            def fake_first(entries):
                first_prompts.append(list(entries))
                return decision

            init_gui._confirm_non_loopback_risk = fake_confirm
            init_gui._confirm_first_start = fake_first
            init_gui._show_generated_token = (
                lambda tok, parent=None: shown_tokens.append(tok)
            )

            # Check risk dialog text: token and TLS are always required
            # beyond loopback, so only the code-execution risk remains.
            title, body_both = init_gui._risk_dialog_text(
                ["192.168.1.50:39280", "10.0.0.5:39281"]
            )
            self.assertEqual(
                title, "MCP Bridge: Listen beyond this machine?"
            )
            self.assertIn("192.168.1.50:39280\n10.0.0.5:39281", body_both)
            self.assertNotIn("No authentication", body_both)
            self.assertNotIn("No encryption", body_both)
            self.assertIn("any client with the bearer token", body_both)
            self.assertIn(
                "Accept these risks and start the bridge?", body_both
            )

            tok_title, tok_body = init_gui._token_dialog_text("tok-sample")
            self.assertEqual(tok_title, "MCP Bridge: Bearer token")
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
            self.assertEqual(start_title, "MCP Bridge: Start the bridge?")
            self.assertIn("full permissions", start_body)
            self.assertIn("only after you accept", start_body)
            _both_title, both_body = init_gui._first_start_dialog_text(
                ["192.168.1.50:39280"]
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

            # 0b. With no certificate/key set, a non-loopback start fails
            # before any dialog and stays off, even when the legacy
            # RequireTls preference is False.
            store["ListenAddress"] = "127.0.0.1:39280\n192.168.1.50:39280"
            store["RequireTls"] = False
            store["TlsCertFile"] = ""
            store["TlsKeyFile"] = ""
            cmd.Activated()
            self.assertFalse(running)
            self.assertEqual(prompts, [])
            self.assertEqual(shown_tokens, [])
            self.assertIn(
                "TLS is required and needs a certificate file and a private "
                "key file.",
                statuses[-1],
            )
            self.assertIn(
                "Select or generate both in Edit → Preferences → MCP Bridge.",
                warnings[-1],
            )
            self.assertNotIn("untick Require TLS", warnings[-1])
            self.assertNotIn(
                "Change the listen addresses in Edit", warnings[-1]
            )
            store["TlsCertFile"] = "cert.pem"
            store["TlsKeyFile"] = "key.pem"
            ssl_patch = mock.patch.object(
                config,
                "build_ssl_context",
                return_value=object(),
            )
            ssl_patch.start()
            self.addCleanup(ssl_patch.stop)

            # 0c. A first start that also has an unaccepted address
            # beyond loopback uses one dialog, and declining saves neither
            # acceptance. The old Apply-to-loopback preference does not
            # make a loopback-only start demand a certificate.
            store["ListenAddress"] = "127.0.0.1:39280\n192.168.1.50:39280"
            decision = False
            cmd.Activated()
            self.assertFalse(running)
            self.assertEqual(prompts, [])
            self.assertEqual(first_prompts, [["192.168.1.50:39280"]])
            self.assertEqual(config.accepted_listen_addresses(), [])
            self.assertFalse(config.start_warning_accepted())
            store["ListenAddress"] = "127.0.0.1:39280"
            store["RequireOnLoopback"] = True
            store["TlsCertFile"] = ""
            store["TlsKeyFile"] = ""
            cmd.Activated()
            self.assertFalse(running)
            self.assertEqual(prompts, [])
            self.assertEqual(first_prompts[-1], [])
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
            store["RequireOnLoopback"] = False
            store["TlsCertFile"] = "cert.pem"
            store["TlsKeyFile"] = "key.pem"
            store["ListenAddress"] = "127.0.0.1:39280\n192.168.1.50:39280"
            decision = False
            cmd.Activated()
            self.assertFalse(running)
            cmd.Activated()
            self.assertFalse(running)
            self.assertEqual(
                prompts, [["192.168.1.50:39280"]]
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
                ["10.0.0.5:39281", "10.0.0.9:39282"],
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

                # 1. Default loopback without cert/key: binds 127.0.0.1:39280
                # as HTTP, removes any stale serving-cert.pem, no warning.
                serving_cert = Path(token_root.name) / "serving-cert.pem"
                serving_cert.write_text("STALE-CERT\n", encoding="utf-8")
                srv.start()
                self.assertEqual(bound, [("127.0.0.1", 39280)])
                self.assertEqual(warnings, [])
                self.assertTrue(srv._servers[0][0].require_token)
                self.assertEqual(
                    srv._servers[0][0].bound_host, "127.0.0.1"
                )
                self.assertEqual(wrapped_sockets, [])
                self.assertFalse(serving_cert.exists())
                srv.stop()

                # 2. List with loopback, LAN, and IPv6 on distinct ports:
                # missing cert raises ValueError and stays off; a token
                # file is not required to bind; failed wrap_socket closes
                # the bound socket; a valid cert wraps every listener
                # (loopback included) with do_handshake_on_connect=False
                # and writes serving-cert.pem beside the token file.
                bound.clear()
                shut_down.clear()
                closed.clear()
                warnings.clear()
                errors.clear()
                store["ListenAddress"] = (
                    "127.0.0.1:39280\n192.168.1.50:39281\n[::1]:39282"
                )
                store["RequireTls"] = False
                with self.assertRaises(ValueError):
                    srv.start()
                self.assertFalse(srv.is_running())
                self.assertEqual(bound, [])
                self.assertIn(
                    "TLS is required and needs a certificate file and a "
                    "private key file.",
                    errors[-1],
                )
                ssl_ctx_patch = mock.patch.object(
                    config,
                    "build_ssl_context",
                    return_value=FakeSSLContext(),
                )
                ssl_ctx_patch.start()
                self.addCleanup(ssl_ctx_patch.stop)
                cert_file_path = Path(token_root.name) / "custom-cert.pem"
                key_file_path = Path(token_root.name) / "custom-key.pem"
                cert_file_path.write_text("CUSTOM-CERT-PEM\n", encoding="utf-8")
                key_file_path.write_text("CUSTOM-KEY-PEM\n", encoding="utf-8")
                store["TlsCertFile"] = str(cert_file_path)
                store["TlsKeyFile"] = str(key_file_path)
                fail_wrap_on = True
                with self.assertRaises(OSError):
                    srv.start(
                        tls_cert_file=str(cert_file_path),
                        tls_key_file=str(key_file_path),
                    )
                fail_wrap_on = False
                self.assertFalse(srv.is_running())
                self.assertEqual(
                    closed,
                    [("127.0.0.1", 39280)],
                )
                self.assertFalse(serving_cert.exists())
                bound.clear()
                closed.clear()

                srv.start(
                    tls_cert_file=str(cert_file_path),
                    tls_key_file=str(key_file_path),
                )
                self.assertTrue(srv.is_running())
                self.assertEqual(
                    bound,
                    [
                        ("127.0.0.1", 39280),
                        ("192.168.1.50", 39281),
                        ("::1", 39282),
                    ],
                )
                self.assertTrue(srv._servers[0][0].require_token)
                self.assertTrue(srv._servers[1][0].require_token)
                self.assertTrue(srv._servers[2][0].require_token)
                self.assertEqual(
                    wrapped_sockets,
                    [
                        "tls(raw-sock-127.0.0.1:39280, "
                        "server_side=True, do_handshake_on_connect=False)",
                        "tls(raw-sock-192.168.1.50:39281, "
                        "server_side=True, do_handshake_on_connect=False)",
                        "tls(raw-sock-::1:39282, "
                        "server_side=True, do_handshake_on_connect=False)",
                    ],
                )
                self.assertTrue(serving_cert.exists())
                self.assertEqual(
                    serving_cert.read_text(encoding="utf-8"),
                    "CUSTOM-CERT-PEM\n",
                )
                self.assertEqual(len(warnings), 1)
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
                # Stopping leaves serving-cert.pem in place until a start
                # without TLS removes it.
                self.assertTrue(serving_cert.exists())
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

                # 2c. On loopback-only, setting certificate and key wraps
                # loopback in TLS and writes serving-cert.pem; clearing them
                # serves HTTP on loopback and removes serving-cert.pem.
                bound.clear()
                closed.clear()
                warnings.clear()
                errors.clear()
                wrapped_sockets.clear()
                store["ListenAddress"] = "127.0.0.1:39280"
                srv.start()
                self.assertTrue(srv.is_running())
                self.assertEqual(
                    wrapped_sockets,
                    [
                        "tls(raw-sock-127.0.0.1:39280, "
                        "server_side=True, do_handshake_on_connect=False)"
                    ],
                )
                self.assertTrue(serving_cert.exists())
                self.assertEqual(warnings, [])
                srv.stop()

                bound.clear()
                closed.clear()
                wrapped_sockets.clear()
                store["RequireOnLoopback"] = True
                store["TlsCertFile"] = ""
                store["TlsKeyFile"] = ""
                srv.start()
                self.assertTrue(srv.is_running())
                self.assertTrue(srv._servers[0][0].require_token)
                self.assertEqual(wrapped_sockets, [])
                self.assertFalse(serving_cert.exists())
                self.assertEqual(warnings, [])
                srv.stop()
                store["RequireOnLoopback"] = False
                store["TlsCertFile"] = str(cert_file_path)
                store["TlsKeyFile"] = str(key_file_path)

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
                handler.server.cert_hosts = {"freecad.lan", "2001:db8::9"}
                handler.headers = {"Host": "192.168.1.50:39281"}
                self.assertTrue(handler._host_ok())
                handler.headers = {"Host": "freecad.lan:39281"}
                self.assertTrue(handler._host_ok())
                handler.headers = {"Host": "FREECAD.LAN"}
                self.assertTrue(handler._host_ok())
                handler.headers = {"Host": "[2001:DB8::9]:39281"}
                self.assertTrue(handler._host_ok())
                handler.headers = {"Host": "other.lan:39281"}
                self.assertFalse(handler._host_ok())
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

    def test_non_loopback_start_fails_when_certificate_does_not_cover_address(
        self,
    ):
        """A start with an entry beyond loopback fails naming the address when
        the certificate does not cover it, and succeeds when every
        non-loopback address in the list is covered."""
        config, _, _ = fresh_config()
        fake_decoded = {
            "subjectAltName": (
                ("IP Address", "192.168.1.50"),
                ("IP Address", "2001:DB8:0:0:0:0:0:5"),
                ("DNS", "freecad.local"),
                ("DNS", "*.cad.example"),
            ),
            "subject": ((("commonName", "192.168.1.50"),),),
        }

        class FakeCtx:
            def load_cert_chain(
                self, certfile=None, keyfile=None, password=None
            ):
                pass

        with (
            mock.patch.object(
                config.ssl, "SSLContext", return_value=FakeCtx()
            ),
            mock.patch.object(
                config.ssl._ssl,
                "_test_decode_cert",
                return_value=fake_decoded,
            ),
        ):
            covered = config.certificate_hosts("cert.pem")
            self.assertEqual(
                covered,
                {
                    "192.168.1.50",
                    "2001:db8::5",
                    "freecad.local",
                    "*.cad.example",
                },
            )
            self.assertTrue(
                config.certificate_covers_host(covered, "192.168.1.50:39280")
            )
            self.assertTrue(
                config.certificate_covers_host(covered, "[2001:db8::5]:39280")
            )
            self.assertTrue(
                config.certificate_covers_host(covered, "FREECAD.LOCAL:39280")
            )
            self.assertTrue(
                config.certificate_covers_host(
                    covered, "box.cad.example:39280"
                )
            )
            self.assertFalse(
                config.certificate_covers_host(
                    covered, "a.b.cad.example:39280"
                )
            )
            self.assertFalse(
                config.certificate_covers_host(covered, "10.0.0.9:39280")
            )
            config.build_ssl_context(
                "cert.pem",
                "key.pem",
                [
                    "127.0.0.1:39280",
                    "192.168.1.50:39280",
                    "[2001:db8::5]:39281",
                    "freecad.local:39282",
                ],
            )
            with self.assertRaises(ValueError) as uncovered:
                config.build_ssl_context(
                    "cert.pem",
                    "key.pem",
                    [
                        "127.0.0.1:39280",
                        "192.168.1.50:39280",
                        "10.0.0.9:39280",
                    ],
                )
            self.assertEqual(
                str(uncovered.exception),
                "The TLS certificate does not cover 10.0.0.9. Select or "
                "generate a certificate in Edit → Preferences → MCP Bridge.",
            )

            # A certificate that is not yet valid fails naming its start date.
            future_decoded = {
                **fake_decoded,
                "notBefore": "Jan  1 00:00:00 2099 GMT",
                "notAfter": "Jan  1 00:00:00 2100 GMT",
            }
            with (
                mock.patch.object(
                    config.ssl._ssl,
                    "_test_decode_cert",
                    return_value=future_decoded,
                ),
                self.assertRaises(ValueError) as not_yet_valid,
            ):
                config.build_ssl_context(
                    "cert.pem",
                    "key.pem",
                    ["192.168.1.50:39280"],
                )
            self.assertEqual(
                str(not_yet_valid.exception),
                "The TLS certificate is not valid until 2099-01-01. Select "
                "or generate a certificate in Edit → Preferences → "
                "MCP Bridge.",
            )

            # An expired certificate fails naming the expiry date.
            expired_decoded = {
                **fake_decoded,
                "notAfter": "Jan  1 00:00:00 2020 GMT",
            }
            with (
                mock.patch.object(
                    config.ssl._ssl,
                    "_test_decode_cert",
                    return_value=expired_decoded,
                ),
                self.assertRaises(ValueError) as expired,
            ):
                config.build_ssl_context(
                    "cert.pem",
                    "key.pem",
                    ["192.168.1.50:39280"],
                )
            self.assertEqual(
                str(expired.exception),
                "The TLS certificate expired on 2020-01-01. Select or "
                "generate a certificate in Edit → Preferences → MCP Bridge.",
            )

            # When _test_decode_cert is missing or raises, start goes ahead
            # with a warning in Report view, and certificate_hosts is empty.
            warnings = []
            config.FreeCAD.Console = types.SimpleNamespace(
                PrintWarning=warnings.append
            )
            with mock.patch.object(
                config.ssl._ssl,
                "_test_decode_cert",
                side_effect=RuntimeError("no decoder"),
            ):
                self.assertEqual(config.certificate_hosts("cert.pem"), set())
                ctx = config.build_ssl_context(
                    "cert.pem",
                    "key.pem",
                    ["192.168.1.50:39280"],
                )
                self.assertIsNotNone(ctx)
            self.assertEqual(
                warnings,
                [
                    "[MCP Bridge] WARNING: could not inspect the TLS "
                    "certificate for expiry or address coverage. Continuing "
                    "without those checks. Connect by the listed address, "
                    "not by a name.\n"
                ],
            )
            warnings.clear()
            with mock.patch.object(
                config.ssl, "_ssl", types.SimpleNamespace()
            ):
                self.assertEqual(config.certificate_hosts("cert.pem"), set())
                ctx = config.build_ssl_context(
                    "cert.pem",
                    "key.pem",
                    ["192.168.1.50:39280"],
                )
                self.assertIsNotNone(ctx)
            self.assertEqual(len(warnings), 1)


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

    def test_freecad_does_not_save_the_permission_mode_box(self):
        """The box is a plain combo box with no preference entry, so
        Preferences' own OK writes nothing for it."""
        box = next(
            widget
            for widget in ET.parse(PAGE).iter("widget")
            if widget.get("name") == "permissionModeComboBox"
        )
        self.assertEqual(box.get("class"), "QComboBox")
        names = {prop.get("name") for prop in box.findall("property")}
        self.assertNotIn("prefEntry", names)
        self.assertNotIn("prefPath", names)
        self.assertNotIn("PermissionMode", page_settings())

    def test_showing_the_box_sets_it_from_the_mode_and_connects_it_once(
        self,
    ):
        """Each time the box shows, it shows the mode in force. Its
        handler is connected the first time only."""
        init_gui = _fresh_init_gui()

        class Box:
            def __init__(self):
                self.index = 0
                self.props = {}
                self.connected = []
                self.activated = types.SimpleNamespace(
                    connect=self.connected.append
                )

            def blockSignals(self, _on):
                pass

            def setCurrentIndex(self, index):
                self.index = index

            def property(self, name):
                return self.props.get(name)

            def setProperty(self, name, value):
                self.props[name] = value

        box = Box()
        with mock.patch(
            "freecad.mcp_bridge.config.unrestricted", return_value=True
        ):
            init_gui._show_mode_in_box(box)
        self.assertEqual(box.index, 1)
        self.assertEqual(len(box.connected), 1)
        with mock.patch(
            "freecad.mcp_bridge.config.unrestricted", return_value=False
        ):
            init_gui._show_mode_in_box(box)
        self.assertEqual(box.index, 0)
        self.assertEqual(len(box.connected), 1)
        # The connected handler is the choice handler.
        chosen = []
        init_gui._on_mode_chosen = lambda b, index: chosen.append((b, index))
        box.connected[0](1)
        self.assertEqual(chosen, [(box, 1)])

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

    def test_the_menu_asks_to_go_unrestricted_and_not_to_go_back(self):
        """Choosing Unrestricted in the button's menu asks once, and only
        Yes switches. Choosing Ask is at once. Each choice refreshes the
        button and an open Preferences box."""
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
            mock.patch.object(init_gui, "show_toggle_state") as button,
            mock.patch.object(init_gui, "_sync_open_mode_box") as box,
        ):
            init_gui._on_mode_menu(True)
            self.assertIs(state["unrestricted"], False)
            init_gui._on_mode_menu(True)
            self.assertIs(state["unrestricted"], True)
            init_gui._on_mode_menu(False)
            self.assertIs(state["unrestricted"], False)
            self.assertEqual(asked, [None, None])
            self.assertEqual(button.call_count, 3)
            self.assertEqual(box.call_count, 3)

    def test_choosing_the_mode_in_force_in_the_menu_does_nothing(self):
        """A click on the mode already in force asks nothing, writes
        nothing and refreshes nothing."""
        init_gui = _fresh_init_gui()
        applied = []
        init_gui._apply_mode_choice = (
            lambda want, box: applied.append((want, box))
        )
        for in_force in (False, True):
            with (
                mock.patch(
                    "freecad.mcp_bridge.config.unrestricted",
                    return_value=in_force,
                ),
                mock.patch.object(init_gui, "show_toggle_state") as button,
            ):
                init_gui._on_mode_menu(in_force)
                self.assertEqual(button.call_count, 0)
        self.assertEqual(applied, [])

    def test_the_menu_names_the_two_modes_and_the_preferences_page(self):
        """The menu's three items, in order. test_verify.py checks, in
        FreeCAD, that each has its icon and none is checkable."""
        self.assertEqual(MENU_ASK, "Permissions: Ask")
        self.assertEqual(MENU_UNRESTRICTED, "Permissions: Unrestricted")
        self.assertEqual(MENU_PREFERENCES, "MCP Bridge Preferences")

    def test_unrestricted_shows_the_button_whatever_the_preference(self):
        """Show the On/Off button hides the button only in Ask mode, so
        Unrestricted stays shown on the status bar."""
        self.assertIs(status_button_shown(True, False), True)
        self.assertIs(status_button_shown(False, False), False)
        self.assertIs(status_button_shown(False, True), True)
        self.assertIs(status_button_shown(True, True), True)
        self.assertEqual(
            page_settings()["ShowStatusButton"]["toolTip"],
            "Display toggle button in the status bar. Always shown "
            "in Unrestricted mode.",
        )


class TlsCertificateGeneratorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import datetime

        _, _, _ = fresh_config()
        from freecad.mcp_bridge import tls_cert

        cls.tls_cert = tls_cert
        cls.now = datetime.datetime(
            2026, 11, 10, 0, 0, 0, tzinfo=datetime.timezone.utc
        )
        cls.hosts = [
            "127.0.0.1",
            "localhost",
            "192.168.1.50",
            "2001:db8::5",
            "freecad.local",
        ]
        cls.cert_pem, cls.key_pem, cls.meta = (
            tls_cert.generate_self_signed_cert(cls.hosts, now=cls.now)
        )
        cls.other_cert_pem, cls.other_key_pem, _ = (
            tls_cert.generate_self_signed_cert(
                ["192.168.1.99", "other.local"], now=cls.now
            )
        )

    def test_generated_rsa_key_arithmetic_permissions_and_openssl_check(
        self,
    ):
        """The generator produces a 2048-bit RSA keypair with valid arithmetic
        and writes tls-key.pem readable by the user only."""
        import math
        import shutil
        import subprocess

        key = self.meta["key"]
        self.assertEqual(key["e"], 65537)
        self.assertNotEqual(key["p"], key["q"])
        self.assertEqual(key["p"].bit_length(), 1024)
        self.assertEqual(key["q"].bit_length(), 1024)
        self.assertEqual(key["p"] >> 1022, 3)
        self.assertEqual(key["q"] >> 1022, 3)
        self.assertEqual(math.gcd(key["p"] - 1, key["e"]), 1)
        self.assertEqual(math.gcd(key["q"] - 1, key["e"]), 1)
        self.assertEqual(key["n"], key["p"] * key["q"])
        self.assertEqual(key["n"].bit_length(), 2048)
        lam = math.lcm(key["p"] - 1, key["q"] - 1)
        self.assertEqual((key["d"] * key["e"]) % lam, 1)

        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.object(
                self.tls_cert,
                "generate_self_signed_cert",
                return_value=(self.cert_pem, self.key_pem, self.meta),
            ):
                c_path, k_path = (
                    self.tls_cert.generate_and_store_certificate(
                        self.hosts, directory=Path(folder)
                    )
                )
            self.assertEqual(c_path, Path(folder) / "tls-cert.pem")
            self.assertEqual(k_path, Path(folder) / "tls-key.pem")
            if os.name == "nt":
                self.assertEqual(_windows_ace_count(k_path), 1)
            else:
                mode = stat.S_IMODE(k_path.stat().st_mode)
                self.assertEqual(mode, 0o600)
            openssl_bin = shutil.which("openssl")
            if openssl_bin is not None:
                res = subprocess.run(
                    [
                        openssl_bin,
                        "rsa",
                        "-in",
                        str(k_path),
                        "-check",
                        "-noout",
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(res.returncode, 0, res.stderr)
                self.assertIn("RSA key ok", res.stdout + res.stderr)

    def test_certificate_signature_validity_and_openssl_verify(self):
        """The certificate is signed with PKCS#1 v1.5 SHA-256 over the TBS
        bytes, valid for 825 days from one day back, and covers each address
        beyond loopback."""
        import datetime
        import hashlib
        import shutil
        import subprocess

        config, _, _ = fresh_config()
        key = self.meta["key"]
        tbs_der = self.meta["tbs_der"]
        sig_bytes = self.meta["signature"]
        self.assertEqual(len(sig_bytes), 256)
        recovered = pow(
            int.from_bytes(sig_bytes, "big"), key["e"], key["n"]
        ).to_bytes(256, "big")
        self.assertEqual(recovered[:2], b"\x00\x01")
        sep = recovered.index(b"\x00", 2)
        self.assertGreaterEqual(sep - 2, 8)
        self.assertEqual(recovered[2:sep], b"\xff" * (sep - 2))
        digest_info = recovered[sep + 1 :]
        expected_prefix = bytes.fromhex(
            "3031300d060960864801650304020105000420"
        )
        self.assertEqual(
            digest_info,
            expected_prefix + hashlib.sha256(tbs_der).digest(),
        )
        self.assertEqual(
            self.meta["not_before"],
            self.now - datetime.timedelta(days=1),
        )
        self.assertEqual(
            self.meta["not_after"] - self.meta["not_before"],
            datetime.timedelta(days=825),
        )
        with tempfile.TemporaryDirectory() as folder:
            c_path = Path(folder) / "tls-cert.pem"
            c_path.write_text(self.cert_pem, encoding="utf-8")
            self.assertEqual(
                config.certificate_hosts(str(c_path)),
                {
                    "127.0.0.1",
                    "localhost",
                    "192.168.1.50",
                    "2001:db8::5",
                    "freecad.local",
                },
            )
            self.assertEqual(
                config.certificate_expiry_date(str(c_path)),
                "2029-02-11",
            )
            self.assertEqual(
                config.certificate_target_hosts(["127.0.0.1:39280"]),
                ["127.0.0.1", "localhost"],
            )
            self.assertEqual(
                config.certificate_target_hosts(
                    [
                        "127.0.0.1:39280",
                        "[::1]:39281",
                        "192.168.1.50:39280",
                        "freecad.local:39281",
                    ]
                ),
                [
                    "127.0.0.1",
                    "localhost",
                    "192.168.1.50",
                    "freecad.local",
                ],
            )
            openssl_bin = shutil.which("openssl")
            if openssl_bin is not None:
                res = subprocess.run(
                    [
                        openssl_bin,
                        "verify",
                        "-check_ss_sig",
                        "-CAfile",
                        str(c_path),
                        str(c_path),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(res.returncode, 0, res.stderr)

    def test_python_ssl_loads_pair_verifies_https_and_refuses_other_cert(
        self,
    ):
        """Python's ssl loads the generated pair, verifies an HTTPS connection
        against the certificate, and refuses a different certificate."""
        import http.server
        import socket
        import ssl
        import threading

        config, _, _ = fresh_config()
        with tempfile.TemporaryDirectory() as folder:
            c_path = Path(folder) / "tls-cert.pem"
            k_path = Path(folder) / "tls-key.pem"
            other_c_path = Path(folder) / "other-cert.pem"
            c_path.write_text(self.cert_pem, encoding="utf-8")
            k_path.write_text(self.key_pem, encoding="utf-8")
            other_c_path.write_text(self.other_cert_pem, encoding="utf-8")

            server_ctx = config.build_ssl_context(
                str(c_path),
                str(k_path),
                [
                    "192.168.1.50:39280",
                    "[2001:db8::5]:39281",
                    "freecad.local:39282",
                ],
            )

            class QuietHandler(http.server.BaseHTTPRequestHandler):
                def do_GET(self):
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"ok")

                def log_message(self, *_args):
                    pass

            httpd = http.server.HTTPServer(("127.0.0.1", 0), QuietHandler)
            httpd.socket = server_ctx.wrap_socket(
                httpd.socket, server_side=True
            )
            port = httpd.server_address[1]
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                good_ctx = ssl.create_default_context(cafile=str(c_path))
                for hostname in ("127.0.0.1", "localhost", "freecad.local"):
                    with socket.create_connection(
                        ("127.0.0.1", port), timeout=5
                    ) as raw:
                        with good_ctx.wrap_socket(
                            raw, server_hostname=hostname
                        ) as tls_sock:
                            tls_sock.sendall(
                                f"GET / HTTP/1.1\r\nHost: {hostname}\r\n"
                                "Connection: close\r\n\r\n".encode("ascii")
                            )
                            reply = tls_sock.recv(1024)
                    self.assertIn(b"200 OK", reply)

                bad_ctx = ssl.create_default_context(cafile=str(other_c_path))
                with self.assertRaises(ssl.SSLCertVerificationError):
                    with socket.create_connection(
                        ("127.0.0.1", port), timeout=5
                    ) as raw:
                        with bad_ctx.wrap_socket(
                            raw, server_hostname="127.0.0.1"
                        ):
                            pass
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=2)

    def test_node_https_verifies_generated_certificate(self):
        """Node's https client verifies a server using the generated pair when
        ca is set to the generated certificate."""
        import http.server
        import shutil
        import subprocess
        import threading

        node_bin = shutil.which("node")
        if node_bin is None:
            self.skipTest("node is not on PATH")
        config, _, _ = fresh_config()
        with tempfile.TemporaryDirectory() as folder:
            c_path = Path(folder) / "tls-cert.pem"
            k_path = Path(folder) / "tls-key.pem"
            c_path.write_text(self.cert_pem, encoding="utf-8")
            k_path.write_text(self.key_pem, encoding="utf-8")
            server_ctx = config.build_ssl_context(
                str(c_path), str(k_path), ["freecad.local:39280"]
            )

            class QuietHandler(http.server.BaseHTTPRequestHandler):
                def do_GET(self):
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"node-ok")

                def log_message(self, *_args):
                    pass

            httpd = http.server.HTTPServer(("127.0.0.1", 0), QuietHandler)
            httpd.socket = server_ctx.wrap_socket(
                httpd.socket, server_side=True
            )
            port = httpd.server_address[1]
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                script = (
                    "const fs = require('node:fs');"
                    "const https = require('node:https');"
                    "const ca = fs.readFileSync(process.argv[1]);"
                    "const port = Number(process.argv[2]);"
                    "https.get({host:'127.0.0.1', port, "
                    "servername:'freecad.local', ca}, (res) => {"
                    "  let body = '';"
                    "  res.on('data', (c) => body += c);"
                    "  res.on('end', () => {"
                    "    console.log(res.statusCode + ':' + body);"
                    "    process.exit(res.statusCode === 200 ? 0 : 1);"
                    "  });"
                    "}).on('error', (e) => {"
                    "  console.error(e); process.exit(2);"
                    "});"
                )
                res = subprocess.run(
                    [node_bin, "-e", script, str(c_path), str(port)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(res.returncode, 0, res.stderr)
                self.assertEqual(res.stdout.strip(), "200:node-ok")
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=2)

    def test_generate_certificate_button_flow_and_replace_confirmation(self):
        """Preferences put the user's own certificate and key fields first with
        'or generate one' beside Generate certificate; the button covers
        127.0.0.1, localhost, and any beyond-loopback entries, uses cert_tools
        when available (RSA 4096 or ECDSA P-256) or falls back to tls_cert with
        an install hint, asks before replacing, and fills both fields without
        saving preferences until OK."""
        tree = ET.parse(PAGE)
        sec_group = next(
            widget
            for widget in tree.iter("widget")
            if widget.get("name") == "securityGroup"
        )
        sec_widgets = [
            widget.get("name")
            for widget in sec_group.iter("widget")
            if widget is not sec_group and widget.get("name")
        ]
        self.assertEqual(
            sec_widgets,
            [
                "tlsCertFileLabel",
                "tlsCertFileChooser",
                "tlsKeyFileLabel",
                "tlsKeyFileChooser",
                "generateCertLabel",
                "generateCertButton",
                "generateTokenButton",
            ],
        )
        gen_label = next(
            widget
            for widget in sec_group.iter("widget")
            if widget.get("name") == "generateCertLabel"
        )
        gen_label_props = {
            prop.get("name"): prop[0].text
            for prop in gen_label.findall("property")
        }
        self.assertEqual(gen_label_props["text"], "or generate one")

        btn = next(
            widget
            for widget in sec_group.iter("widget")
            if widget.get("name") == "generateCertButton"
        )
        props = {
            prop.get("name"): prop[0].text
            for prop in btn.findall("property")
        }
        self.assertEqual(props["text"], "Generate certificate")
        self.assertEqual(
            props["toolTip"],
            "Generate a self-signed TLS certificate.",
        )
        self.assertLess(len(props["toolTip"]), 60)

        client_note = next(
            widget
            for widget in tree.iter("widget")
            if widget.get("name") == "clientNote"
        )
        client_note_props = {
            prop.get("name"): prop[0].text
            for prop in client_note.findall("property")
        }
        self.assertIn(
            "Without a TLS certificate, a loopback entry uses",
            client_note_props["text"],
        )
        self.assertIn(
            "with a certificate, every entry uses HTTPS.",
            client_note_props["text"],
        )

        init_gui = _fresh_init_gui()
        config, _, _ = fresh_config()
        store = {}

        class DictParams:
            def GetString(self, name, default=""):
                return store.get(name, default)

            def SetString(self, name, val):
                store[name] = val

        class FakeChooser:
            def __init__(self, initial=""):
                self._props = {"fileName": initial}

            def property(self, name):
                return self._props.get(name, "")

            def setProperty(self, name, val):
                self._props[name] = val

            def fileName(self):
                return self._props.get("fileName", "")

            def setFileName(self, val):
                self._props["fileName"] = val

            def findChild(self, *_args):
                return None

        class FakeLabel:
            def __init__(self):
                self.text = ""

            def setText(self, val):
                self.text = val

        class FakePrefPage:
            def __init__(self):
                self.cert_chooser = FakeChooser()
                self.key_chooser = FakeChooser()
                self.note_label = FakeLabel()
                self._children = {
                    "tlsCertFileChooser": self.cert_chooser,
                    "tlsKeyFileChooser": self.key_chooser,
                    "listenAddressNote": self.note_label,
                }

            def findChild(self, _cls, name=""):
                return self._children.get(name)

            def accept(self):
                store["TlsCertFile"] = self.cert_chooser.fileName()
                store["TlsKeyFile"] = self.key_chooser.fileName()

        page = FakePrefPage()
        messages = []
        replace_answers = []
        generated_hosts = []
        init_gui._show_cert_message = (
            lambda title, body, warning, object_name, parent=None: (
                messages.append((title, body, warning, object_name))
            )
        )
        init_gui._confirm_replace_certificate = (
            lambda parent=None: replace_answers.pop(0)
        )

        def fake_generate_self_signed(hosts, now=None):
            generated_hosts.append(list(hosts))
            return self.cert_pem, self.key_pem, self.meta

        with tempfile.TemporaryDirectory() as folder:
            with (
                mock.patch.dict(
                    os.environ, {"FREECAD_MCP_TOKEN_DIR": folder}
                ),
                mock.patch.object(
                    config, "_params", return_value=DictParams()
                ),
                mock.patch.object(
                    self.tls_cert,
                    "generate_self_signed_cert",
                    side_effect=fake_generate_self_signed,
                ),
                mock.patch.dict(
                    sys.modules, {"freecad.mcp_bridge.cert_tools": None}
                ),
            ):
                # 1. Loopback only with no external tool found -> falls back
                # to tls_cert covering 127.0.0.1 and localhost, names expiry
                # and platform install hint, and fills both fields without
                # saving preferences yet.
                store["ListenAddress"] = "127.0.0.1:39280\n[::1]:39281"
                result = init_gui.generate_and_show_certificate(parent=page)
                expected_cert = str(Path(folder) / "tls-cert.pem")
                expected_key = str(Path(folder) / "tls-key.pem")
                self.assertEqual(result, (expected_cert, expected_key))
                self.assertEqual(
                    generated_hosts[-1], ["127.0.0.1", "localhost"]
                )
                self.assertEqual(page.cert_chooser.fileName(), expected_cert)
                self.assertEqual(page.key_chooser.fileName(), expected_key)
                self.assertIn(expected_cert, page.note_label.text)
                self.assertNotIn("TlsCertFile", store)
                self.assertNotIn("TlsKeyFile", store)
                self.assertEqual(len(messages), 1)
                self.assertEqual(
                    messages[-1][0], "MCP Bridge: TLS certificate"
                )
                self.assertFalse(messages[-1][2])
                self.assertIn(expected_cert, messages[-1][1])
                self.assertIn("127.0.0.1", messages[-1][1])
                self.assertIn("localhost", messages[-1][1])
                self.assertIn("2029-02-11 (825 days)", messages[-1][1])
                self.assertIn(
                    "No certificate tool was found on this machine",
                    messages[-1][1],
                )

                # 2. Address beyond loopback, existing files from step 1 ->
                # asks to replace; Yes generates covering 127.0.0.1,
                # localhost, 192.168.1.50, and freecad.local.
                messages.clear()
                store["ListenAddress"] = (
                    "127.0.0.1:39280\n192.168.1.50:39280\nfreecad.local:39281"
                )
                replace_answers.append(True)
                result = init_gui.generate_and_show_certificate(parent=page)
                self.assertEqual(result, (expected_cert, expected_key))
                self.assertEqual(
                    generated_hosts[-1],
                    [
                        "127.0.0.1",
                        "localhost",
                        "192.168.1.50",
                        "freecad.local",
                    ],
                )
                self.assertEqual(len(messages), 1)
                self.assertIn("127.0.0.1", messages[-1][1])
                self.assertIn("localhost", messages[-1][1])
                self.assertIn("192.168.1.50", messages[-1][1])
                self.assertIn("freecad.local", messages[-1][1])

                # 3. Existing files/settings -> asks before replacing; No
                # leaves existing files untouched; Yes replaces the files on
                # disk and fills the fields, while Cancel in Preferences
                # leaves the old saved paths and OK saves the new ones.
                rep_title, rep_body = init_gui._cert_replace_dialog_text()
                self.assertEqual(
                    rep_title, "MCP Bridge: Replace TLS certificate?"
                )
                self.assertIn(
                    "Any MCP client that trusts the present certificate "
                    "will need the new certificate file.",
                    rep_body,
                )
                store["TlsCertFile"] = "/old/cert.pem"
                store["TlsKeyFile"] = "/old/key.pem"
                page.cert_chooser.setFileName("/old/cert.pem")
                page.key_chooser.setFileName("/old/key.pem")
                (Path(folder) / "tls-cert.pem").write_text(
                    "keep-existing", encoding="utf-8"
                )
                messages.clear()
                replace_answers.append(False)
                self.assertIsNone(
                    init_gui.generate_and_show_certificate(parent=page)
                )
                self.assertEqual(messages, [])
                self.assertEqual(page.cert_chooser.fileName(), "/old/cert.pem")
                self.assertEqual(page.key_chooser.fileName(), "/old/key.pem")
                self.assertEqual(
                    (Path(folder) / "tls-cert.pem").read_text(
                        encoding="utf-8"
                    ),
                    "keep-existing",
                )
                replace_answers.append(True)
                result2 = init_gui.generate_and_show_certificate(parent=page)
                self.assertEqual(result2, (expected_cert, expected_key))
                self.assertEqual(page.cert_chooser.fileName(), expected_cert)
                self.assertEqual(page.key_chooser.fileName(), expected_key)
                # Before OK (or if the user clicks Cancel), saved preferences
                # keep the old paths while the files on disk stay as generated.
                self.assertEqual(store["TlsCertFile"], "/old/cert.pem")
                self.assertEqual(store["TlsKeyFile"], "/old/key.pem")
                self.assertEqual(
                    (Path(folder) / "tls-cert.pem").read_text(
                        encoding="utf-8"
                    ),
                    self.cert_pem,
                )
                # Clicking OK in Preferences saves the filled field values.
                page.accept()
                self.assertEqual(store["TlsCertFile"], expected_cert)
                self.assertEqual(store["TlsKeyFile"], expected_key)

                # 4. When cert_tools.find_tools() returns tools, the button
                # offers the choice of tool and key type (RSA 4096 or
                # ECDSA P-256), calls cert_tools.generate(tool, key_type,
                # hosts, cert_path, key_path), applies user-only permissions
                # to tls-key.pem, and omits the fallback install hint.
                tool_calls = []

                def stub_generate(
                    tool, key_type, hosts, cert_path, key_path
                ):
                    tool_calls.append(
                        (
                            tool,
                            key_type,
                            list(hosts),
                            Path(cert_path),
                            Path(key_path),
                        )
                    )
                    Path(cert_path).write_text(
                        self.cert_pem, encoding="utf-8"
                    )
                    Path(key_path).write_text(
                        self.key_pem, encoding="utf-8"
                    )

                stub_cert_tools = types.SimpleNamespace(
                    find_tools=lambda: [
                        {"label": "OpenSSL (PATH)", "path": "/usr/bin/openssl"},
                        {
                            "label": "Git for Windows OpenSSL",
                            "path": "C:/Program Files/Git/usr/bin/openssl.exe",
                        },
                    ],
                    generate=stub_generate,
                )
                with mock.patch.dict(
                    sys.modules,
                    {"freecad.mcp_bridge.cert_tools": stub_cert_tools},
                ):
                    for chosen_idx, chosen_key_type in (
                        (0, "RSA 4096"),
                        (1, "ECDSA P-256"),
                    ):
                        messages.clear()
                        replace_answers.append(True)
                        init_gui._choose_cert_tool_and_key_type = (
                            lambda tools, parent=None, idx=chosen_idx, kt=chosen_key_type: (
                                tools[idx],
                                kt,
                            )
                        )
                        res = init_gui.generate_and_show_certificate(
                            parent=page
                        )
                        self.assertEqual(res, (expected_cert, expected_key))
                        self.assertEqual(
                            tool_calls[-1],
                            (
                                stub_cert_tools.find_tools()[chosen_idx],
                                chosen_key_type,
                                [
                                    "127.0.0.1",
                                    "localhost",
                                    "192.168.1.50",
                                    "freecad.local",
                                ],
                                Path(expected_cert),
                                Path(expected_key),
                            ),
                        )
                        if os.name == "nt":
                            self.assertEqual(
                                _windows_ace_count(Path(expected_key)), 1
                            )
                        else:
                            mode = stat.S_IMODE(
                                Path(expected_key).stat().st_mode
                            )
                            self.assertEqual(mode, 0o600)
                        self.assertEqual(len(messages), 1)
                        self.assertNotIn(
                            "No certificate tool was found",
                            messages[-1][1],
                        )
                        self.assertIn(
                            "2029-02-11 (825 days)", messages[-1][1]
                        )


if __name__ == "__main__":
    unittest.main()
