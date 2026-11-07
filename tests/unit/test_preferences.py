"""Requirements on the bridge's preferences (preferences.ui, config.py).

Each test name, and the first line of its docstring, states one requirement
as a user setting up the bridge experiences it. The preference page is read
from the .ui file FreeCAD builds it from; config.py is run against a
stand-in for FreeCAD's parameter store holding no values, as a fresh
profile does.
"""

import importlib
import re
import sys
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

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "freecad" / "mcp_bridge" / "Resources" / "Ui" / "preferences.ui"
# Where a preference widget's prefPath is rooted in FreeCAD's parameters.
PREF_ROOT = "User parameter:BaseApp/Preferences"
SETTINGS = (
    "ListenAddress",
    "Port",
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
        """The page shows defaults 127.0.0.1, 39280, 15 s, 32 K characters,
        5 min, and the status-bar button shown on the right."""
        settings = page_settings()
        self.assertEqual(settings["ListenAddress"]["text"], "127.0.0.1")
        spin_names = (
            "Port",
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
                "Port": (39280, None),
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
        """With nothing saved: 127.0.0.1, port 39280, 15 s, 32,768 chars,
        5 min, button shown on the right."""
        config, params, groups = fresh_config()
        self.assertEqual(config.listen_address(), "127.0.0.1")
        self.assertEqual(config.port(), 39280)
        self.assertEqual(config.max_response_timeout_ms(), 15_000)
        self.assertEqual(config.max_page_size_chars(), 32_768)
        self.assertEqual(config.max_history_retention_ms(), 5 * 60_000)
        self.assertIs(config.show_status_button(), True)
        self.assertEqual(config.status_button_position(), 0)
        # What it read is what the preference page saves.
        self.assertEqual(set(groups), {PREF_GROUP})
        self.assertEqual(params.read, set(SETTINGS))


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
    def test_per_address_acceptance_is_stored_and_queried_by_address(self):
        """Loopback is always accepted; non-loopback addresses are stored per
        address in AcceptedListenAddresses."""
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
            self.assertTrue(config.is_address_accepted("127.0.0.1"))
            self.assertTrue(config.is_address_accepted("localhost"))
            self.assertFalse(config.is_address_accepted("192.168.1.50"))
            self.assertFalse(config.is_address_accepted("0.0.0.0"))

            config.accept_address("192.168.1.50")
            self.assertTrue(config.is_address_accepted("192.168.1.50"))
            self.assertFalse(config.is_address_accepted("0.0.0.0"))
            self.assertEqual(
                store["AcceptedListenAddresses"], "192.168.1.50"
            )

            config.accept_address("0.0.0.0")
            self.assertTrue(config.is_address_accepted("192.168.1.50"))
            self.assertTrue(config.is_address_accepted("0.0.0.0"))
            self.assertEqual(
                store["AcceptedListenAddresses"], "192.168.1.50,0.0.0.0"
            )

    def test_non_loopback_start_prompts_once_per_address_and_warns_each_start(
        self,
    ):
        """Turning on at a non-loopback address prompts once per address,
        stays off if declined, and warns in the status bar on every start."""
        store = {"ListenAddress": "192.168.1.50"}
        running = False
        prompts = []
        statuses = []
        notifications = []
        decision = False

        class DictParams:
            def GetString(self, name, default=""):
                return store.get(name, default)

            def SetString(self, name, value):
                store[name] = value

            def GetInt(self, _name, default=0):
                return default

            def GetBool(self, _name, default=False):
                return default

        class FakeStatusBar:
            def showMessage(self, msg):
                statuses.append(msg)

        class FakeMainWindow:
            def statusBar(self):
                return FakeStatusBar()

        freecad = types.SimpleNamespace(
            ParamGet=lambda _g: DictParams(),
            Console=types.SimpleNamespace(
                PrintNotification=notifications.append,
                PrintWarning=lambda *_: None,
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
            start=lambda: None,
            stop=lambda: None,
        )

        def start_bridge():
            nonlocal running
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
            importlib.import_module("freecad.mcp_bridge.config")
            sys.modules.pop("freecad.mcp_bridge.init_gui", None)
            init_gui = importlib.import_module("freecad.mcp_bridge.init_gui")
            init_gui.show_toggle_state = lambda: None

            def fake_confirm(address, port):
                prompts.append((address, port))
                return decision

            init_gui._confirm_non_loopback_risk = fake_confirm
            title, body = init_gui._risk_dialog_text("192.168.1.50", 39280)
            self.assertEqual(
                title, "MCP Bridge — Listen on 192.168.1.50:39280?"
            )
            self.assertIn("No authentication", body)
            self.assertIn("No encryption", body)
            self.assertIn("Code execution", body)
            self.assertIn("(and 127.0.0.1:39280)", body)

            cmd = init_gui.MCPBridgeCommand()

            # 1. Decline: bridge stays off and address is not accepted.
            decision = False
            cmd.Activated()
            self.assertFalse(running)
            self.assertEqual(prompts, [("192.168.1.50", 39280)])
            self.assertEqual(statuses[-1], "MCP Bridge: Offline")
            self.assertNotIn("AcceptedListenAddresses", store)

            # 2. Accept: bridge starts, address is saved, status bar warns.
            decision = True
            cmd.Activated()
            self.assertTrue(running)
            self.assertEqual(len(prompts), 2)
            self.assertEqual(
                store.get("AcceptedListenAddresses"), "192.168.1.50"
            )
            self.assertEqual(
                statuses[-1],
                "MCP Bridge: Listening on 192.168.1.50:39280 and "
                "127.0.0.1:39280 (warning: no authentication or encryption)",
            )

            # 3. Stop and start again at the same address: no prompt, warns.
            cmd.Activated()
            self.assertFalse(running)
            cmd.Activated()
            self.assertTrue(running)
            self.assertEqual(len(prompts), 2)
            self.assertIn(
                "(warning: no authentication or encryption)", statuses[-1]
            )

            # 4. Switch to a second non-loopback address: prompts for that one.
            cmd.Activated()
            store["ListenAddress"] = "0.0.0.0"
            cmd.Activated()
            self.assertTrue(running)
            self.assertEqual(prompts[-1], ("0.0.0.0", 39280))
            self.assertEqual(
                store.get("AcceptedListenAddresses"), "192.168.1.50,0.0.0.0"
            )
            sys.modules.pop("freecad.mcp_bridge.init_gui", None)

    def test_http_server_binds_loopback_alongside_another_address_and_warns(
        self,
    ):
        """HttpServer binds 127.0.0.1 alongside a specific listen address,
        binds 0.0.0.0 once for all interfaces, and logs a Report view warning
        on each non-loopback start."""
        store = {"ListenAddress": "127.0.0.1", "Port": 39280}
        messages = []
        warnings = []
        bound = []
        closed = []

        class DictParams:
            def GetString(self, name, default=""):
                return store.get(name, default)

            def GetInt(self, name, default=0):
                return store.get(name, default)

        class FakeHTTPServer:
            def __init__(self, addr, _handler):
                self.addr = addr
                self.executor = None
                bound.append(addr)

            def serve_forever(self):
                pass

            def shutdown(self):
                pass

            def server_close(self):
                closed.append(self.addr)

        class FakeThread:
            def __init__(self, target=None, name=None, daemon=None):
                self._alive = False

            def start(self):
                self._alive = True

            def is_alive(self):
                return self._alive

        freecad = types.SimpleNamespace(
            ParamGet=lambda _g: DictParams(),
            Console=types.SimpleNamespace(
                PrintMessage=messages.append,
                PrintWarning=warnings.append,
                PrintDeveloperError=lambda *_: None,
            ),
        )
        fake_executor_mod = types.SimpleNamespace(
            Executor=lambda: types.SimpleNamespace(shutdown=lambda: None)
        )
        stubs = {
            "FreeCAD": freecad,
            "freecad.mcp_bridge.executor": fake_executor_mod,
        }
        with mock.patch.dict(sys.modules, stubs):
            sys.modules.pop("freecad.mcp_bridge.config", None)
            importlib.import_module("freecad.mcp_bridge.config")
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

                # 1. Default loopback: binds 127.0.0.1 only, no warning.
                srv.start()
                self.assertEqual(bound, [("127.0.0.1", 39280)])
                self.assertEqual(warnings, [])
                srv.stop()

                # 2. Specific address: binds 127.0.0.1 and 192.168.1.50, warns.
                bound.clear()
                closed.clear()
                store["ListenAddress"] = "192.168.1.50"
                srv.start()
                self.assertEqual(
                    bound,
                    [("127.0.0.1", 39280), ("192.168.1.50", 39280)],
                )
                self.assertEqual(len(warnings), 1)
                self.assertIn(
                    "WARNING: listening on 192.168.1.50:39280 and "
                    "127.0.0.1:39280 with no authentication or encryption.",
                    warnings[0],
                )
                srv.stop()
                self.assertEqual(
                    closed,
                    [("192.168.1.50", 39280), ("127.0.0.1", 39280)],
                )

                # 3. 0.0.0.0: binds 0.0.0.0 once, warns.
                bound.clear()
                warnings.clear()
                store["ListenAddress"] = "0.0.0.0"
                srv.start()
                self.assertEqual(bound, [("0.0.0.0", 39280)])
                self.assertEqual(len(warnings), 1)
                self.assertIn(
                    "WARNING: listening on 0.0.0.0:39280 and 127.0.0.1:39280",
                    warnings[0],
                )
                srv.stop()
            sys.modules.pop("freecad.mcp_bridge.http_server", None)


if __name__ == "__main__":
    unittest.main()
