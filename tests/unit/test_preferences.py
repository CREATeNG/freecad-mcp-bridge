"""Requirements on the bridge's preferences (preferences.ui, config.py).

Each test name, and the first line of its docstring, states one requirement
as a user setting up the bridge experiences it. The preference page is read
from the .ui file FreeCAD builds it from; config.py is run against a
stand-in for FreeCAD's parameter store holding no values, as a fresh
profile does.
"""

import ast
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
    PREF_GROUP,
    TOOLBAR_OBJECT_NAME,
)

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "freecad" / "mcp_bridge" / "Resources" / "Ui" / "preferences.ui"
# Where a preference widget's prefPath is rooted in FreeCAD's parameters.
PREF_ROOT = "User parameter:BaseApp/Preferences"
SETTINGS = (
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
        """The page shows defaults 39280, 15 s, 32 K characters, 5 min, and
        the status-bar button shown on the right."""
        settings = page_settings()
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
        """With nothing saved: port 39280, 15 s, 32,768 chars, 5 min, button
        shown on the right."""
        config, params, groups = fresh_config()
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
    def test_toolbar_shows_by_default_and_persists_user_hide_and_show(self):
        """The toolbar shows on a fresh install, stays hidden across workbench
        switches and restarts when unchecked, and stays shown when checked
        again."""
        config, _, _ = fresh_config()
        self.assertIs(config.show_toolbar(), True)

        saved = {}
        messages = []

        class FakeSignal:
            def __init__(self):
                self._slots = []

            def connect(self, slot):
                self._slots.append(slot)

            def emit(self, *args):
                for slot in list(self._slots):
                    slot(*args)

        class FakeAction:
            def __init__(self, text="", checked=False):
                self._text = text
                self._checked = checked
                self.triggered = FakeSignal()

            def text(self):
                return self._text

            def setIcon(self, _icon):
                pass

            def setCheckable(self, _checkable):
                pass

            def setChecked(self, checked):
                self._checked = bool(checked)

            def isChecked(self):
                return self._checked

            def setToolTip(self, _tip):
                pass

        class FakeToolBar:
            def __init__(self, title, parent=None):
                self._title = title
                self._parent = parent
                self._object_name = ""
                self._visible = False
                self._actions = []
                self._toggle_action = FakeAction(title, checked=False)

                def _on_trigger(checked):
                    self.setVisible(checked)

                self._toggle_action.triggered.connect(_on_trigger)

            def windowTitle(self):
                return self._title

            def objectName(self):
                return self._object_name

            def setObjectName(self, name):
                self._object_name = name

            def addAction(self, label):
                action = FakeAction(label)
                self._actions.append(action)
                return action

            def actions(self):
                return list(self._actions)

            def toggleViewAction(self):
                return self._toggle_action

            def setVisible(self, visible):
                self._visible = bool(visible)
                self._toggle_action.setChecked(self._visible)

            def show(self):
                self.setVisible(True)

            def hide(self):
                self.setVisible(False)

            def isVisible(self):
                return self._visible

            def user_trigger_toggle_view(self):
                new_state = not self._toggle_action.isChecked()
                self._toggle_action.setChecked(new_state)
                self._toggle_action.triggered.emit(new_state)

        class FakeMainWindow:
            def __init__(self):
                self._toolbars = []

            def findChildren(self, cls):
                if cls is FakeToolBar:
                    return list(self._toolbars)
                return []

            def addToolBar(self, tb):
                self._toolbars.append(tb)

            def update(self):
                pass

            def isVisible(self):
                return True

        class FakeParams:
            def GetBool(self, name, default):
                return saved.get(name, default)

            def SetBool(self, name, value):
                saved[name] = bool(value)

        init_gui = _fresh_init_gui()
        current_mw = FakeMainWindow()
        freecad = types.SimpleNamespace(
            MCPBridgeIconPath="icon.svg",
            MCPBridgeCommand=COMMAND_ID,
            MCPBridgeInjectUi=init_gui.inject_ui,
            ParamGet=lambda _group: FakeParams(),
            Console=types.SimpleNamespace(
                PrintMessage=messages.append,
                PrintWarning=lambda *_: None,
            ),
        )
        gui = types.SimpleNamespace(
            getMainWindow=lambda: current_mw,
            runCommand=lambda *_: None,
        )
        qtcore = types.SimpleNamespace(
            QTimer=types.SimpleNamespace(singleShot=lambda *_: None)
        )
        qtgui = types.SimpleNamespace(QIcon=lambda path: path)
        qtwidgets = types.SimpleNamespace(QToolBar=FakeToolBar)
        stubs = {
            "FreeCAD": freecad,
            "FreeCADGui": gui,
            "PySide": types.SimpleNamespace(
                QtCore=qtcore, QtGui=qtgui, QtWidgets=qtwidgets
            ),
            "PySide.QtCore": qtcore,
            "PySide.QtGui": qtgui,
            "PySide.QtWidgets": qtwidgets,
        }

        with (
            mock.patch.dict(sys.modules, stubs),
            mock.patch.object(init_gui, "_ensure_status_button"),
            mock.patch.object(init_gui, "show_toggle_state"),
        ):
            sys.modules["freecad.mcp_bridge.config"] = config
            importlib.reload(config)

            # 1. Fresh install: toolbar is created and shown.
            self.assertTrue(init_gui.inject_ui())
            tb = current_mw.findChildren(FakeToolBar)[0]
            self.assertEqual(tb.objectName(), TOOLBAR_OBJECT_NAME)
            self.assertTrue(tb.isVisible())
            self.assertNotIn("ShowToolbar", saved)
            self.assertEqual(messages, ["[MCP Bridge] Toolbar ready.\n"])

            # 2. Programmatic hide (e.g. window minimize) does not save a
            # user preference.
            tb.hide()
            self.assertNotIn("ShowToolbar", saved)
            tb.show()

            # 3. User unchecks "MCP Bridge" in the toolbar menu: hides and
            # persists across workbench switches (re-injection) and restart.
            tb.user_trigger_toggle_view()
            self.assertFalse(tb.isVisible())
            self.assertIs(saved.get("ShowToolbar"), False)

            self.assertFalse(init_gui.inject_ui())
            self.assertFalse(tb.isVisible())

            # Simulated FreeCAD restart with ShowToolbar=False saved.
            current_mw = FakeMainWindow()
            freecad.MCPBridgeUiReady = False
            self.assertFalse(init_gui.inject_ui())
            tb_after_restart = current_mw.findChildren(FakeToolBar)[0]
            self.assertFalse(tb_after_restart.isVisible())
            self.assertFalse(init_gui.inject_ui())
            self.assertFalse(tb_after_restart.isVisible())

            # 4. User checks "MCP Bridge" again: shows and persists across
            # restart.
            tb_after_restart.user_trigger_toggle_view()
            self.assertTrue(tb_after_restart.isVisible())
            self.assertIs(saved.get("ShowToolbar"), True)

            current_mw = FakeMainWindow()
            freecad.MCPBridgeUiReady = False
            self.assertTrue(init_gui.inject_ui())
            tb_shown_again = current_mw.findChildren(FakeToolBar)[0]
            self.assertTrue(tb_shown_again.isVisible())

    def test_command_is_registered_under_the_mcp_bridge_category(self):
        """Gui.addCommand registers the command under the MCP_Bridge Customize
        category and with an importable module for macro recording."""
        recorded = []

        def add_command(name, cmd):
            fn = sys._getframe(1).f_code.co_filename.replace("\\", "/")
            tail = fn.rsplit("/", 1)[-1]
            module = tail.rsplit(".", 1)[0] if "." in tail else tail
            match = re.search(r"/Mod/(\w+)/", fn) or re.search(
                r"/Ext/freecad/(\w+)/", fn
            )
            group = match.group(1) if match else module
            recorded.append((name, group, module, cmd))

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
            sys.modules.pop("freecad.mcp_bridge.init_gui", None)
            importlib.import_module("freecad.mcp_bridge.init_gui")
            sys.modules.pop("freecad.mcp_bridge.init_gui", None)
            self.assertEqual(len(recorded), 1)
            name, group, module, cmd = recorded[0]
            self.assertEqual(name, COMMAND_ID)
            self.assertEqual(group, TOOLBAR_OBJECT_NAME)
            self.assertEqual(module, "freecad.mcp_bridge")
            macro_source = f"import {module}\nGui.runCommand({name!r}, 0)\n"
            ast.parse(macro_source)
            self.assertIsNotNone(importlib.import_module(module))
            self.assertEqual(
                cmd.GetResources()["MenuText"], "MCP Bridge On/Off"
            )


if __name__ == "__main__":
    unittest.main()
