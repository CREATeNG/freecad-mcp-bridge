"""The Preferences page loads and saves its preference widgets itself.

FreeCAD loads and saves a `Gui::Pref…` widget only on a page registered
as a .ui file. The MCP Bridge page is a Python class, so `pref_pages`
reads and writes each widget's setting. These tests use stand-in
widgets that answer as PySide's wrappers of FreeCAD's widgets do.
"""

import sys
import types
import unittest
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from freecad.mcp_bridge.constants import LOG_PREFIX, PREF_GROUP

PAGE = (
    Path(__file__).resolve().parents[2]
    / "freecad"
    / "mcp_bridge"
    / "Resources"
    / "Ui"
    / "preferences.ui"
)
PREF_PATH = "Mod/freecad-mcp-bridge"


class ByteArray:
    """PySide's QByteArray, as far as the page reads it."""

    def __init__(self, text):
        self._bytes = text.encode("utf-8")

    def data(self):
        return self._bytes


class Meta:
    def __init__(self, class_name):
        self._class_name = class_name

    def className(self):
        return self._class_name


class Widget:
    """A preference widget. `entry` and `path` are QByteArray-like."""

    kind = "QWidget"

    def __init__(self, name, entry=None, path=PREF_PATH, plain_text=False):
        self._name = name
        self._props = {}
        if entry is not None:
            wrap = (lambda text: text) if plain_text else ByteArray
            self._props["prefEntry"] = wrap(entry)
            self._props["prefPath"] = wrap(path)

    def objectName(self):
        return self._name

    def metaObject(self):
        return Meta(self.kind)

    def property(self, name):
        return self._props.get(name)

    def setProperty(self, name, value):
        self._props[name] = value

    def findChild(self, _kind, _name=""):
        return None


class Spin(Widget):
    kind = "Gui::PrefSpinBox"

    def __init__(self, name, entry, shown, **kwargs):
        super().__init__(name, entry, **kwargs)
        self._value = shown

    def value(self):
        return self._value

    def setValue(self, value):
        self._value = value


class Check(Widget):
    kind = "Gui::PrefCheckBox"

    def __init__(self, name, entry, shown, **kwargs):
        super().__init__(name, entry, **kwargs)
        self._checked = shown

    def isChecked(self):
        return self._checked

    def setChecked(self, checked):
        self._checked = checked


class Combo(Widget):
    kind = "Gui::PrefComboBox"

    def __init__(self, name, entry, shown, count=2, **kwargs):
        super().__init__(name, entry, **kwargs)
        self._index = shown
        self._count = count

    def count(self):
        return self._count

    def currentIndex(self):
        return self._index

    def setCurrentIndex(self, index):
        self._index = index


class Chooser(Widget):
    kind = "Gui::PrefFileChooser"

    def __init__(self, name, entry, **kwargs):
        super().__init__(name, entry, **kwargs)
        self._props["fileName"] = ""


class LineEdit(Widget):
    """A FreeCAD preference widget the page has no handling for."""

    kind = "Gui::PrefLineEdit"


class Form:
    """The page's form as the .ui file makes it, defaults and all."""

    def __init__(self, extra=()):
        self.timeout = Spin(
            "timeoutSpinBox", "MaxResponseTimeoutSeconds", 15
        )
        self.page_size = Spin("pageSizeSpinBox", "MaxPageSize", 32)
        self.retention = Spin(
            "retentionSpinBox", "MaxHistoryRetentionMinutes", 5
        )
        self.show = Check(
            "showStatusButtonCheckBox", "ShowStatusButton", True
        )
        self.position = Combo(
            "statusButtonPositionComboBox",
            "StatusButtonPosition",
            0,
            plain_text=True,
        )
        self.cert = Chooser("tlsCertFileChooser", "TlsCertFile")
        self.key = Chooser("tlsKeyFileChooser", "TlsKeyFile")
        self.widgets = [
            Widget("tlsCertFileLabel"),
            self.timeout,
            self.page_size,
            self.retention,
            self.show,
            self.position,
            self.cert,
            self.key,
            *extra,
        ]

    def findChildren(self, _kind):
        return list(self.widgets)

    def findChild(self, _kind, name=""):
        for widget in self.widgets:
            if widget.objectName() == name:
                return widget
        return None


class Params:
    """FreeCAD's parameter group over a dict."""

    def __init__(self):
        self.values = {}

    def _get(self, name, default):
        return self.values.get(name, default)

    GetInt = GetBool = GetString = _get

    def _set(self, name, value):
        self.values[name] = value

    SetInt = SetBool = SetString = _set


@contextmanager
def page_code():
    """`pref_pages` over an empty parameter store.

    Yields the module, the store, the groups opened and the Report
    view warnings.
    """
    params, groups, warnings = Params(), [], []

    def param_get(group):
        groups.append(group)
        return params

    freecad = types.SimpleNamespace(
        ParamGet=param_get,
        Console=types.SimpleNamespace(PrintWarning=warnings.append),
    )
    widgets = types.ModuleType("PySide.QtWidgets")
    widgets.QWidget = type("QWidget", (), {})
    stubs = {
        "FreeCAD": freecad,
        "PySide": types.ModuleType("PySide"),
        "PySide.QtWidgets": widgets,
    }
    with mock.patch.dict(sys.modules, stubs):
        from freecad.mcp_bridge import pref_pages

        yield pref_pages, params, groups, warnings


class PreferenceWidgetTests(unittest.TestCase):
    def test_a_profile_with_nothing_saved_shows_the_forms_defaults(self):
        """With nothing saved, each widget keeps the form's default."""
        with page_code() as (pref_pages, _params, groups, warnings):
            form = Form()
            pref_pages.restore_prefs(form)
        self.assertEqual(form.timeout.value(), 15)
        self.assertEqual(form.page_size.value(), 32)
        self.assertEqual(form.retention.value(), 5)
        self.assertIs(form.show.isChecked(), True)
        self.assertEqual(form.position.currentIndex(), 0)
        self.assertEqual(form.cert.property("fileName"), "")
        self.assertEqual(form.key.property("fileName"), "")
        self.assertEqual(set(groups), {PREF_GROUP})
        self.assertEqual(warnings, [])

    def test_saved_settings_are_shown_when_the_page_opens(self):
        """Each saved setting is shown, the two certificate paths too."""
        with page_code() as (pref_pages, params, _groups, warnings):
            params.values.update(
                {
                    "MaxResponseTimeoutSeconds": 60,
                    "MaxPageSize": 64,
                    "MaxHistoryRetentionMinutes": 30,
                    "ShowStatusButton": False,
                    "StatusButtonPosition": 1,
                    "TlsCertFile": "C:/bridge/tls-cert.pem",
                    "TlsKeyFile": "C:/bridge/tls-key.pem",
                }
            )
            form = Form()
            pref_pages.restore_prefs(form)
        self.assertEqual(form.timeout.value(), 60)
        self.assertEqual(form.page_size.value(), 64)
        self.assertEqual(form.retention.value(), 30)
        self.assertIs(form.show.isChecked(), False)
        self.assertEqual(form.position.currentIndex(), 1)
        self.assertEqual(
            form.cert.property("fileName"), "C:/bridge/tls-cert.pem"
        )
        self.assertEqual(
            form.key.property("fileName"), "C:/bridge/tls-key.pem"
        )
        self.assertEqual(warnings, [])

    def test_ok_saves_each_setting_in_the_type_freecad_uses(self):
        """OK writes an Int, a Bool or a String under each entry name."""
        with page_code() as (pref_pages, params, groups, warnings):
            form = Form()
            pref_pages.restore_prefs(form)
            form.timeout.setValue(120)
            form.page_size.setValue(128)
            form.retention.setValue(10)
            form.show.setChecked(False)
            form.position.setCurrentIndex(1)
            form.cert.setProperty("fileName", "C:/certs/cert.pem")
            form.key.setProperty("fileName", "C:/certs/key.pem")
            pref_pages.save_prefs(form)
        self.assertEqual(
            params.values,
            {
                "MaxResponseTimeoutSeconds": 120,
                "MaxPageSize": 128,
                "MaxHistoryRetentionMinutes": 10,
                "ShowStatusButton": False,
                "StatusButtonPosition": 1,
                "TlsCertFile": "C:/certs/cert.pem",
                "TlsKeyFile": "C:/certs/key.pem",
            },
        )
        for name in (
            "MaxResponseTimeoutSeconds",
            "MaxPageSize",
            "MaxHistoryRetentionMinutes",
            "StatusButtonPosition",
        ):
            self.assertIs(type(params.values[name]), int, name)
        self.assertIs(type(params.values["ShowStatusButton"]), bool)
        self.assertIs(type(params.values["TlsCertFile"]), str)
        self.assertIs(type(params.values["TlsKeyFile"]), str)
        self.assertEqual(set(groups), {PREF_GROUP})
        self.assertEqual(warnings, [])

    def test_generated_paths_are_saved_on_ok_with_neither_saved_before(self):
        """With neither path saved, Generate's fill then OK saves both."""
        from tests.unit.test_preferences import _fresh_init_gui

        init_gui = _fresh_init_gui()
        with page_code() as (pref_pages, params, _groups, warnings):
            form = Form()
            pref_pages.restore_prefs(form)
            self.assertNotIn("TlsCertFile", params.values)
            self.assertNotIn("TlsKeyFile", params.values)
            init_gui._set_chooser_file_name(
                form, "tlsCertFileChooser", "C:/bridge/tls-cert.pem"
            )
            init_gui._set_chooser_file_name(
                form, "tlsKeyFileChooser", "C:/bridge/tls-key.pem"
            )
            pref_pages.save_prefs(form)
        self.assertEqual(
            params.values["TlsCertFile"], "C:/bridge/tls-cert.pem"
        )
        self.assertEqual(params.values["TlsKeyFile"], "C:/bridge/tls-key.pem")
        self.assertEqual(warnings, [])

    def test_a_path_the_user_clears_is_saved_empty(self):
        """Clearing a certificate field and pressing OK empties the path."""
        with page_code() as (pref_pages, params, _groups, _warnings):
            params.values["TlsCertFile"] = "C:/bridge/tls-cert.pem"
            params.values["TlsKeyFile"] = "C:/bridge/tls-key.pem"
            form = Form()
            pref_pages.restore_prefs(form)
            form.cert.setProperty("fileName", "")
            form.key.setProperty("fileName", "  ")
            pref_pages.save_prefs(form)
        self.assertEqual(params.values["TlsCertFile"], "")
        self.assertEqual(params.values["TlsKeyFile"], "")

    def test_cancel_saves_nothing(self):
        """A page that is loaded and never saved writes no setting."""
        with page_code() as (pref_pages, params, _groups, _warnings):
            form = Form()
            pref_pages.restore_prefs(form)
            form.timeout.setValue(120)
            form.cert.setProperty("fileName", "C:/certs/cert.pem")
        self.assertEqual(params.values, {})

    def test_reset_shows_the_forms_defaults_again(self):
        """With the settings removed, a second load shows the defaults."""
        with page_code() as (pref_pages, params, _groups, _warnings):
            params.values.update(
                {
                    "MaxResponseTimeoutSeconds": 60,
                    "ShowStatusButton": False,
                    "StatusButtonPosition": 1,
                    "TlsCertFile": "C:/bridge/tls-cert.pem",
                }
            )
            form = Form()
            pref_pages.restore_prefs(form)
            self.assertEqual(form.timeout.value(), 60)
            params.values.clear()
            pref_pages.restore_prefs(form)
        self.assertEqual(form.timeout.value(), 15)
        self.assertIs(form.show.isChecked(), True)
        self.assertEqual(form.position.currentIndex(), 0)
        self.assertEqual(form.cert.property("fileName"), "")

    def test_a_saved_position_outside_the_list_shows_the_default(self):
        """A saved index the combo box does not have shows its default."""
        for saved in (2, -1, 99):
            with self.subTest(saved=saved):
                with page_code() as (pref_pages, params, _groups, _warn):
                    params.values["StatusButtonPosition"] = saved
                    form = Form()
                    pref_pages.restore_prefs(form)
                self.assertEqual(form.position.currentIndex(), 0)

    def test_a_widget_the_page_does_not_handle_gives_one_warning(self):
        """An entry name on an unhandled widget is a Report view warning."""
        stray = LineEdit("strayLineEdit", "StrayEntry")
        no_path = Spin("noPathSpinBox", "NoPathEntry", 3, path="")
        with page_code() as (pref_pages, params, _groups, warnings):
            form = Form(extra=(stray, no_path))
            pref_pages.restore_prefs(form)
            pref_pages.save_prefs(form)
        self.assertEqual(
            warnings,
            [
                f"{LOG_PREFIX} The Preferences page does not load or save "
                "strayLineEdit: it does not handle a Gui::PrefLineEdit "
                "widget.\n",
                f"{LOG_PREFIX} The Preferences page does not load or save "
                "noPathSpinBox: the widget has no preference path.\n",
            ],
        )
        self.assertNotIn("StrayEntry", params.values)
        self.assertNotIn("NoPathEntry", params.values)
        # The seven the page handles are still saved.
        self.assertEqual(len(params.values), 7)

    def test_the_page_handles_every_preference_widget_on_its_form(self):
        """Each widget in preferences.ui with an entry name is handled."""
        from freecad.mcp_bridge import pref_pages

        kinds = {}
        for widget in ET.parse(PAGE).iter("widget"):
            for prop in widget.findall("property"):
                if prop.get("name") == "prefEntry":
                    kinds[prop[0].text] = widget.get("class")
        self.assertEqual(
            sorted(kinds),
            [
                "MaxHistoryRetentionMinutes",
                "MaxPageSize",
                "MaxResponseTimeoutSeconds",
                "ShowStatusButton",
                "StatusButtonPosition",
                "TlsCertFile",
                "TlsKeyFile",
            ],
        )
        for entry, kind in kinds.items():
            with self.subTest(entry=entry):
                self.assertIn(kind, pref_pages._PREF_KINDS)


if __name__ == "__main__":
    unittest.main()
