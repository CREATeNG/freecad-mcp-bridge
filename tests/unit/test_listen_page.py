"""Rows, ticks and marks for Listen addresses, against a made-up list."""

import sys
import types
import unittest
from unittest import mock


def _load():
    """Import against the same stand-in FreeCAD the preference tests use."""
    from tests.unit.test_preferences import fresh_config

    config, _params, _groups = fresh_config()
    sys.modules["freecad.mcp_bridge.config"] = config
    sys.modules["FreeCAD"] = config.FreeCAD
    import freecad.mcp_bridge as pkg

    pkg.config = config
    from freecad.mcp_bridge import listen_page

    return config, listen_page


config, listen_page = _load()


def tearDownModule():
    """Drop the stand-in so the other unit modules import their own."""
    sys.modules.pop("freecad.mcp_bridge.config", None)
    sys.modules.pop("freecad.mcp_bridge.listen_page", None)
    sys.modules.pop("FreeCAD", None)
    import freecad.mcp_bridge as pkg

    if hasattr(pkg, "config"):
        del pkg.config
from freecad.mcp_bridge.constants import DEFAULT_PORT

RECORDS = [
    ("Loopback", "127.0.0.1"),
    ("Loopback", "::1"),
    ("Loopback", "fe80::1"),
    ("Ethernet", "192.168.1.20"),
    ("Ethernet", "fe80::abcd"),
    ("Wi-Fi", "2001:db8::5"),
    ("Ethernet", "192.168.1.20"),
]


class AddressRowTests(unittest.TestCase):
    def test_rows_are_ipv4_first_and_skip_link_local_ipv6(self):
        """Up interfaces, one row per address, ::1 kept, fe80 left out."""
        rows = listen_page.address_rows(RECORDS)
        self.assertEqual(
            rows,
            [
                ("127.0.0.1 (Loopback)", "127.0.0.1"),
                ("192.168.1.20 (Ethernet)", "192.168.1.20"),
                ("::1 (Loopback)", "::1"),
                ("2001:db8::5 (Wi-Fi)", "2001:db8::5"),
            ],
        )

    def test_ticks_follow_the_list_and_use_the_loopback_port(self):
        """127.0.0.1 starts ticked. A tick adds that port. Untick removes
        every port for the address. A typed entry ticks the row."""
        raw = "127.0.0.1:4000"
        rows = listen_page.address_rows(RECORDS)
        ticked = [
            address
            for _label, address in rows
            if listen_page.row_is_ticked(raw, address)
        ]
        self.assertEqual(ticked, ["127.0.0.1"])
        self.assertTrue(listen_page.row_is_ticked("", "127.0.0.1"))
        self.assertFalse(listen_page.row_is_ticked("", "192.168.1.20"))
        added = listen_page.with_address(raw, "192.168.1.20")
        self.assertIn("192.168.1.20:4000", listen_page.split_entries(added))
        self.assertTrue(
            listen_page.row_is_ticked(added, "192.168.1.20")
        )
        no_loop = listen_page.with_address("", "192.168.1.20")
        self.assertEqual(
            listen_page.split_entries(no_loop),
            [
                f"127.0.0.1:{DEFAULT_PORT}",
                f"192.168.1.20:{DEFAULT_PORT}",
            ],
        )
        typed = "192.168.1.20:9"
        self.assertTrue(listen_page.row_is_ticked(typed, "192.168.1.20"))
        cleared = listen_page.without_address(
            "192.168.1.20:9\n192.168.1.20:4000\n127.0.0.1:4000",
            "192.168.1.20",
        )
        self.assertEqual(cleared, "127.0.0.1:4000")

    def test_marks_name_what_will_stop_a_start(self):
        """A bad entry, a wildcard, a foreign address, and an empty list."""
        machine = listen_page.machine_addresses(
            listen_page.address_rows(RECORDS)
        )
        empty = listen_page.entry_marks("", machine)
        self.assertEqual(
            empty,
            [
                "The list is empty, so the bridge starts "
                "on 127.0.0.1:39280."
            ],
        )
        marks = listen_page.entry_marks(
            "not-an-entry\n0.0.0.0:39280\n10.1.1.1:39280\n"
            "freecad.local:39280\n127.0.0.1:39280",
            machine,
        )
        self.assertTrue(any("no port" in line for line in marks))
        self.assertTrue(
            any("not a specific address" in line for line in marks)
        )
        self.assertIn(
            "10.1.1.1 is not an address of this machine. "
            "The bridge will not start with it.",
            marks,
        )
        self.assertFalse(any("freecad.local" in line for line in marks))
        self.assertFalse(any("127.0.0.1 is not" in line for line in marks))
        wider = set(machine)
        wider.add("fe80::1")
        self.assertFalse(
            listen_page.entry_marks("[fe80::1]:39280", wider)
        )
        self.assertFalse(
            listen_page.entry_marks("127.9.9.9:39280", machine)
        )

    def test_a_host_name_that_does_not_resolve_here_is_only_a_note(self):
        """Lookup is injected. The note does not say the start stops."""
        machine = listen_page.machine_addresses(
            listen_page.address_rows(RECORDS)
        )

        def resolve(name):
            if name == "here.lan":
                return ["192.168.1.20"]
            if name == "away.lan":
                return ["10.9.9.9"]
            raise OSError("no such name")

        notes = listen_page.host_name_notes(
            ["here.lan", "away.lan", "missing.lan"], resolve, machine
        )
        self.assertEqual(
            notes,
            [
                "away.lan does not resolve to this machine. "
                "The bridge still starts.",
                "missing.lan does not resolve to this machine. "
                "The bridge still starts.",
            ],
        )

    def test_the_page_names_a_difference_from_the_running_bridge(self):
        """Off says nothing. A different list names both and the apply step."""
        self.assertEqual(
            listen_page.difference_text("127.0.0.1:39280", None), ""
        )
        self.assertEqual(
            listen_page.difference_text(
                "127.0.0.1:39280", ["127.0.0.1:39280"]
            ),
            "",
        )
        text = listen_page.difference_text(
            "127.0.0.1:39280\n192.168.1.20:39280",
            ["127.0.0.1:39280"],
        )
        self.assertEqual(
            text,
            "The bridge is listening on 127.0.0.1:39280. "
            "Listen addresses has 127.0.0.1:39280, 192.168.1.20:39280. "
            "Click OK, then turn the bridge off and on again, "
            "to apply the change.",
        )
        self.assertEqual(
            listen_page.difference_text("", ["127.0.0.1:39280"]),
            "",
        )

    def test_host_names_round_trip_and_cover_a_generated_certificate(self):
        """Adding a name lists it once. The certificate hosts include it."""
        self.assertEqual(
            listen_page.with_name("My-PC", "my-pc"), "My-PC"
        )
        self.assertTrue(listen_page.name_is_listed("My-PC\nlan", "LAN"))
        self.assertEqual(
            listen_page.without_name("My-PC\nlan", "my-pc"), "lan"
        )
        hosts = config.certificate_target_hosts(
            ["127.0.0.1:39280", "[::1]:39281"],
            ["My-PC", "lan.example"],
        )
        self.assertEqual(
            hosts,
            ["127.0.0.1", "localhost", "::1", "my-pc", "lan.example"],
        )
        from freecad.mcp_bridge import tls_cert

        cert_pem, _key, _meta = tls_cert.generate_self_signed_cert(
            hosts
        )
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cert.pem"
            path.write_text(cert_pem, encoding="utf-8")
            covered = config.certificate_hosts(str(path))
        self.assertIn("my-pc", covered)
        self.assertIn("lan.example", covered)
        self.assertIn("127.0.0.1", covered)


def _edit(text):
    """A stand-in for the Listen addresses box."""
    edit = types.SimpleNamespace(_text=text, _props={})
    edit.property = lambda name: edit._props.get(name)
    edit.setProperty = lambda name, value: edit._props.__setitem__(
        name, value
    )
    edit.toPlainText = lambda: edit._text
    edit.setPlainText = lambda value: setattr(edit, "_text", value)
    return edit


class ListenPageWiringTests(unittest.TestCase):
    def test_a_tick_adds_an_entry_and_an_untick_removes_it(self):
        """The row's tick writes the address, and untick takes it out."""
        from tests.unit.test_preferences import _fresh_init_gui

        init_gui = _fresh_init_gui()
        edit = _edit("127.0.0.1:39280")
        init_gui._toggle_address(edit, "192.168.1.20", True)
        self.assertIn("192.168.1.20:39280", edit.toPlainText())
        init_gui._toggle_address(edit, "192.168.1.20", False)
        self.assertNotIn("192.168.1.20", edit.toPlainText())
        self.assertIn("127.0.0.1:39280", edit.toPlainText())

    def test_the_own_name_row_writes_the_name_and_refreshes_the_notes(self):
        """Ticking or unticking the own-name row refreshes the notes."""
        from tests.unit.test_preferences import _fresh_init_gui

        init_gui = _fresh_init_gui()
        widgets = types.ModuleType("PySide.QtWidgets")
        widgets.QTextEdit = object
        pyside = types.ModuleType("PySide")
        pyside.QtWidgets = widgets
        sys.modules["PySide"] = pyside
        sys.modules["PySide.QtWidgets"] = widgets
        self.addCleanup(lambda: sys.modules.pop("PySide", None))
        self.addCleanup(lambda: sys.modules.pop("PySide.QtWidgets", None))
        names = types.SimpleNamespace(text="")
        names.toPlainText = lambda: names.text
        names.setPlainText = lambda value: setattr(names, "text", value)
        edit = _edit("")
        window = types.SimpleNamespace(
            findChild=lambda kind, name: (
                names if name == "hostNamesTextEdit" else None
            )
        )
        edit.window = lambda: window
        seen = []
        init_gui._refresh_listen_page = (
            lambda _edit, lookup=False: seen.append(lookup)
        )
        with mock.patch(
            "freecad.mcp_bridge.listen_page.own_host_name",
            return_value="my-pc",
        ):
            init_gui._toggle_own_name(edit, True)
            self.assertEqual(names.text, "my-pc")
            init_gui._toggle_own_name(edit, False)
            self.assertEqual(names.text, "")
        self.assertEqual(seen, [True, True])

    def test_refresh_ticks_the_default_and_labels_this_machine(self):
        """An empty box ticks 127.0.0.1, and the own-name row is labelled."""
        from tests.unit.test_preferences import _fresh_init_gui

        init_gui = _fresh_init_gui()

        class Signal:
            def connect(self, fn):
                self.fn = fn

        class Box:
            def __init__(self, label, parent=None):
                self.label = label
                self.checked = False
                self.props = {}
                self.toggled = Signal()

            def setProperty(self, key, value):
                self.props[key] = value

            def property(self, key):
                return self.props.get(key)

            def setChecked(self, value):
                self.checked = value

            def isChecked(self):
                return self.checked

            def setText(self, value):
                self.label = value

        class Label:
            def __init__(self):
                self.text = ""

            def setText(self, value):
                self.text = value

        class Names:
            def __init__(self):
                self.text = ""

            def toPlainText(self):
                return self.text

        class Layout:
            def __init__(self):
                self.widgets = []

            def count(self):
                return len(self.widgets)

            def takeAt(self, index):
                widget = self.widgets.pop(index)
                return types.SimpleNamespace(widget=lambda: widget)

            def addWidget(self, widget):
                self.widgets.append(widget)

        layout = Layout()
        holder = types.SimpleNamespace(layout=lambda: layout)
        holder.findChildren = lambda kind: [
            widget
            for widget in layout.widgets
            if isinstance(widget, kind)
        ]
        own = Box("This machine")
        marks = Label()
        diff = Label()
        names = Names()
        children = {
            "addressRows": holder,
            "listenAddressMarks": marks,
            "listeningDifferenceLabel": diff,
            "hostNamesTextEdit": names,
            "ownHostNameCheckBox": own,
        }
        widgets = types.ModuleType("PySide.QtWidgets")
        widgets.QCheckBox = Box
        widgets.QLabel = Label
        widgets.QTextEdit = object
        widgets.QWidget = object
        pyside = types.ModuleType("PySide")
        sys.modules["PySide"] = pyside
        sys.modules["PySide.QtWidgets"] = widgets
        self.addCleanup(lambda: sys.modules.pop("PySide", None))
        self.addCleanup(
            lambda: sys.modules.pop("PySide.QtWidgets", None)
        )
        edit = _edit("")
        edit.window = lambda: types.SimpleNamespace(
            findChild=lambda kind, name="": children.get(name)
        )

        def records(only_up=True):
            rows = [
                ("Loopback Pseudo-Interface 1", "127.0.0.1"),
                ("Ethernet 2", "192.168.1.20"),
            ]
            if not only_up:
                rows.append(("Down", "10.2.2.2"))
            return rows

        init_gui._qt_interface_records = records
        import freecad.mcp_bridge as pkg

        saved_mod = sys.modules.get("freecad.mcp_bridge.bridge")
        saved_attr = getattr(pkg, "bridge", None)
        bridge_mod = types.ModuleType("freecad.mcp_bridge.bridge")
        bridge_mod.is_running = lambda: False
        sys.modules["freecad.mcp_bridge.bridge"] = bridge_mod
        pkg.bridge = bridge_mod

        def _restore_bridge():
            if saved_mod is None:
                sys.modules.pop("freecad.mcp_bridge.bridge", None)
            else:
                sys.modules["freecad.mcp_bridge.bridge"] = saved_mod
            if saved_attr is not None:
                pkg.bridge = saved_attr
            elif hasattr(pkg, "bridge"):
                delattr(pkg, "bridge")

        self.addCleanup(_restore_bridge)
        with mock.patch(
            "freecad.mcp_bridge.listen_page.own_host_name",
            return_value="my-pc",
        ):
            init_gui._refresh_listen_page(edit, lookup=False)
            self.assertEqual(
                [box.label for box in layout.widgets],
                [
                    "127.0.0.1 (Loopback Pseudo-Interface 1)",
                    "192.168.1.20 (Ethernet 2)",
                ],
            )
            self.assertTrue(layout.widgets[0].isChecked())
            self.assertFalse(layout.widgets[1].isChecked())
            self.assertEqual(own.label, "This machine: my-pc")
            self.assertFalse(own.isChecked())
            self.assertIn("The list is empty", marks.text)
            names.text = "my-pc"
            init_gui._refresh_listen_page(edit, lookup=False)
            self.assertTrue(own.isChecked())
            edit.setPlainText("10.2.2.2:39280")
            init_gui._refresh_listen_page(edit, lookup=False)
            self.assertNotIn("10.2.2.2 is not an address", marks.text)
            edit.setPlainText("10.9.9.9:39280")
            init_gui._refresh_listen_page(edit, lookup=False)
            self.assertIn(
                "10.9.9.9 is not an address of this machine. "
                "The bridge will not start with it.",
                marks.text,
            )
