"""The listen list: three kinds of row, Add's refusals, and a start."""

import unittest


def _load():
    """Import after the preference tests' stand-in for FreeCAD."""
    import sys

    from tests.unit.test_preferences import fresh_config

    config, _params, _groups = fresh_config()
    sys.modules["freecad.mcp_bridge.config"] = config
    sys.modules["FreeCAD"] = config.FreeCAD
    from freecad.mcp_bridge import listeners

    return listeners


listeners = _load()


def tearDownModule():
    """Drop the stand-in so other unit modules import their own."""
    import sys

    sys.modules.pop("freecad.mcp_bridge.config", None)
    sys.modules.pop("freecad.mcp_bridge.listeners", None)
    sys.modules.pop("freecad.mcp_bridge.listen_page", None)
    sys.modules.pop("FreeCAD", None)

from freecad.mcp_bridge.listeners import (
    DUPLICATE_ROW_REFUSAL,
    HOST_NAME_LINE,
    KIND_ADAPTER,
    KIND_ADDRESS,
    KIND_NAME,
    adapter_choices,
    adapter_ipv4,
    adapter_row,
    add_refusal,
    address_row,
    already_listed,
    bind_entries,
    can_edit,
    cells,
    default_rows,
    dump_adapters,
    edit_refusal,
    host_already_accepted,
    legacy_row,
    name_row,
    replace_row,
    risk_line,
    row_note,
    rows_from_store,
    start_notes,
    with_row,
    without_index,
)


RECORDS = [
    ("Ethernet 2", "192.168.1.20"),
    ("Ethernet 2", "fe80::1"),
    ("Ethernet 2", "169.254.1.8"),
    ("Loopback", "127.0.0.1"),
    ("Wi-Fi", "2001:db8::5"),
    ("vEthernet (WSL)*", "10.0.0.8"),
]


class ListenListTests(unittest.TestCase):
    def test_a_new_user_sees_one_fixed_row(self):
        """Nothing saved is 127.0.0.1:39280, a fixed IP address."""
        rows = rows_from_store("", "", "", False)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].kind, KIND_ADDRESS)
        self.assertEqual(cells(rows[0], None), (
            "127.0.0.1", "39280", "Fixed IP address",
        ))

    def test_only_host_names_open_no_listener(self):
        """Host names alone are not something the bridge can listen on."""
        rows = rows_from_store("", "", "studio.example", True)
        self.assertEqual([row.kind for row in rows], [KIND_NAME])
        self.assertEqual(bind_entries(rows, {}), [])

    def test_a_saved_empty_list_stays_empty(self):
        """OK on an empty list does not put the default row back."""
        rows = rows_from_store("", "", "", True)
        self.assertEqual(rows, [])
        self.assertEqual(bind_entries(rows, {}), [])

    def test_three_kinds_and_the_port_column(self):
        """An adapter, a fixed address and a host name read in columns."""
        adapter = adapter_row("Ethernet 2", 39280)
        fixed = address_row("127.0.0.1", 39280)
        name = name_row("workshop-pc")
        self.assertEqual(
            cells(adapter, "192.168.1.20"),
            ("Ethernet 2", "39280", "Adapter, 192.168.1.20"),
        )
        self.assertEqual(
            cells(fixed, None),
            ("127.0.0.1", "39280", "Fixed IP address"),
        )
        self.assertEqual(cells(name, None), ("workshop-pc", "", "Host name"))
        stored = dump_adapters([adapter])
        self.assertEqual(stored, "39280\tEthernet 2")
        self.assertNotIn(":", stored.split("\t", 1)[1])

    def test_an_adapter_name_is_not_parsed_out_of_a_line(self):
        """Spaces, brackets and an asterisk survive the stored form."""
        row = adapter_row("vEthernet (WSL)*", 4000)
        text = dump_adapters([row])
        loaded = rows_from_store("", text, "", True)
        self.assertEqual(loaded[0].adapter, "vEthernet (WSL)*")
        self.assertEqual(loaded[0].port, 4000)

    def test_add_refuses_a_wildcard_a_name_and_an_unreadable_address(self):
        """Add says why, and a duplicate changes nothing."""
        self.assertIn(
            "wildcard", add_refusal(KIND_ADDRESS, "0.0.0.0", "80", [])
        )
        self.assertIn("wildcard", add_refusal(KIND_ADDRESS, "::", "80", []))
        self.assertIn(
            "not a name",
            add_refusal(KIND_ADDRESS, "workshop-pc", "80", []),
        )
        self.assertIn(
            "can't be read",
            add_refusal(KIND_ADDRESS, "1.2", "80", []),
        )
        self.assertEqual(
            add_refusal(KIND_NAME, "", "80", []),
            "Enter a host name.",
        )
        self.assertIn("port", add_refusal(KIND_ADDRESS, "10.0.0.1", "0", []))
        rows = default_rows()
        again = address_row("127.0.0.1", 39280)
        self.assertTrue(already_listed(rows, again))
        self.assertEqual(with_row(rows, again), rows)

    def test_remove_can_take_the_last_row(self):
        """The list may be empty."""
        self.assertEqual(without_index(default_rows(), 0), [])

    def test_an_adapter_follows_its_ipv4_between_starts(self):
        """A later start binds the address the adapter has then."""
        row = adapter_row("Ethernet 2", 39280)
        first = bind_entries([row], adapter_ipv4(RECORDS))
        self.assertEqual(first, ["192.168.1.20:39280"])
        moved = [("Ethernet 2", "10.1.1.5")]
        second = bind_entries([row], adapter_ipv4(moved))
        self.assertEqual(second, ["10.1.1.5:39280"])
        self.assertEqual(
            adapter_choices(RECORDS),
            [
                ("Ethernet 2 (192.168.1.20)", "Ethernet 2"),
                ("Loopback (127.0.0.1)", "Loopback"),
                ("vEthernet (WSL)* (10.0.0.8)", "vEthernet (WSL)*"),
            ],
        )

    def test_a_name_with_a_port_is_not_a_listener(self):
        """The page and the start say to replace it."""
        rows = rows_from_store("workshop-pc:39280", "", "", True)
        self.assertEqual(rows[0].kind, "legacy")
        self.assertEqual(bind_entries(rows, {}), [])
        notes = start_notes(rows, {}, set(), set())
        self.assertEqual(
            notes,
            [
                "workshop-pc:39280 is a name with a port, which "
                "is not a listen address. Remove this row, add "
                "an adapter or a fixed IP address, and add the "
                "name as a host name.",
            ],
        )
        self.assertEqual(cells(rows[0], None)[2], "Old entry")

    def test_an_accepted_address_ignores_the_port(self):
        """A changed port does not ask again. The key is the address."""
        row = adapter_row("Ethernet 2", 4000)
        self.assertIn(
            "Ethernet 2 (192.168.1.20:4000)",
            risk_line(row, "192.168.1.20"),
        )
        stored = ["192.168.1.20:39280"]
        self.assertTrue(host_already_accepted(stored, "192.168.1.20"))
        self.assertFalse(host_already_accepted(stored, "10.0.0.1"))

    def test_a_fixed_address_off_this_machine_is_skipped(self):
        """The row is named, and the bridge listens on the rest."""
        row = address_row("10.9.9.9", 39280)
        kept = address_row("127.0.0.1", 39280)
        note = (
            "10.9.9.9 is not an address of this machine, so "
            "the bridge opens no listener on it."
        )
        self.assertEqual(
            row_note(row, {"192.168.1.20"}, set(), {}), note
        )
        self.assertEqual(
            start_notes(
                [row, kept], {}, {"192.168.1.20"}, set()
            ),
            [note],
        )
        self.assertEqual(
            bind_entries([row, kept], {}, {"192.168.1.20"}),
            ["127.0.0.1:39280"],
        )

    def test_an_adapter_with_no_ipv4_is_skipped_and_marked(self):
        """An up adapter with no IPv4 is not a listener, and the row says so."""
        from freecad.mcp_bridge.listeners import row_note

        row = adapter_row("Ethernet 2", 39280)
        note = row_note(row, set(), {"Ethernet 2"}, {})
        self.assertEqual(
            note,
            "Ethernet 2 has no usable IPv4 address, so the bridge "
            "opens no listener on it.",
        )
        self.assertEqual(bind_entries([row], {}), [])
        self.assertEqual(
            start_notes([row], {}, set(), {"Ethernet 2"}), [note]
        )
        down = row_note(row, set(), set(), {})
        self.assertEqual(
            down,
            "Ethernet 2 is down or is not an adapter of this "
            "machine, so the bridge opens no listener on it.",
        )
        wild = address_row("0.0.0.0", 39280)
        self.assertEqual(
            row_note(wild, {"127.0.0.1"}, set(), {}),
            "0.0.0.0 is a wildcard, so the bridge opens no "
            "listener on it.",
        )
        bare = rows_from_store("10.0.0.5", "", "", True)[0]
        self.assertEqual(
            row_note(bare, set(), set(), {}),
            "10.0.0.5 has no port. Remove this row, and add "
            "10.0.0.5 as a fixed IP address with its port.",
        )

    def test_the_same_address_and_port_is_one_listener(self):
        """An adapter and a fixed row of the same endpoint bind once."""
        adapter = adapter_row("Ethernet 2", 39280)
        fixed = address_row("192.168.1.20", 39280)
        ipv4 = {"Ethernet 2": "192.168.1.20"}
        self.assertEqual(
            bind_entries([fixed, adapter], ipv4),
            ["192.168.1.20:39280"],
        )

    def test_an_empty_page_is_not_filled_with_the_default(self):
        """The running line uses the rows as they are."""
        from freecad.mcp_bridge.listeners import difference_text

        text = difference_text([], ["127.0.0.1:39280"])
        self.assertIn("Addresses has nothing", text)
        self.assertNotIn("127.0.0.1:39280. Addresses has 127", text)

    def test_add_refuses_a_bad_ipv6_address_and_a_spaced_name(self):
        """A bad IPv6 address is unreadable. A name with a space is refused."""
        self.assertIn(
            "can't be read",
            add_refusal(KIND_ADDRESS, "2001:db8:::1", "80", []),
        )
        self.assertIn(
            "without a space",
            add_refusal(KIND_NAME, "my pc", "80", []),
        )
        self.assertIn(
            "comma",
            add_refusal(KIND_NAME, "a,b", "80", []),
        )
        self.assertIn(
            "semicolon",
            add_refusal(KIND_NAME, "a;b", "80", []),
        )
        self.assertIn(
            "not a name",
            add_refusal(KIND_ADDRESS, "myhost:39280", "80", []),
        )

    def test_a_dead_mode_box_is_dropped(self):
        """A mode box deleted with its page is not kept."""
        from freecad.mcp_bridge.pref_pages import (
            live_mode_boxes,
            remember_mode_box,
        )

        class Dead:
            def objectName(self):
                raise RuntimeError("deleted")

        remember_mode_box(Dead())
        self.assertEqual(live_mode_boxes(), [])

    def test_the_page_loads_saves_cancels_and_opens_again(self):
        """Load shows the save. Cancel leaves it. A second open reads it."""
        import sys
        import types
        from unittest import mock

        widgets = types.ModuleType("PySide.QtWidgets")
        for name in (
            "QWidget",
            "QTableWidget",
            "QComboBox",
            "QLabel",
            "QPushButton",
            "QToolButton",
            "QSizePolicy",
            "QTableWidgetItem",
        ):
            setattr(widgets, name, type(name, (), {}))
        pyside = types.ModuleType("PySide")
        with mock.patch.dict(
            sys.modules,
            {"PySide": pyside, "PySide.QtWidgets": widgets},
        ):
            self._page_round_trip()

    def _page_round_trip(self):
        """Load, save, cancel and open the page class again."""
        from unittest import mock

        class Form:
            def findChild(self, _kind, _name=""):
                return None

            def findChildren(self, _kind):
                return []

        from freecad.mcp_bridge import pref_pages
        from freecad.mcp_bridge.listeners import adapter_row

        with mock.patch.object(
            pref_pages, "load_form", return_value=Form()
        ):
            first = pref_pages.MCPBridgePreferencesPage()
            first._show_mode = lambda: None
            first.loadSettings()
            self.assertEqual(first.rows[0].host, "127.0.0.1")
            first.rows = [adapter_row("Ethernet 2", 4000)]
            first._choices = lambda: []
            first._own_name = lambda: ""
            first._fill = lambda: None
            first._table = lambda: type(
                "T", (), {"currentRow": lambda self: 0}
            )()
            with mock.patch.object(
                pref_pages, "ask_add", return_value=name_row("workshop-pc")
            ):
                first.add_listen()
            self.assertEqual(len(first.rows), 2)
            with mock.patch.object(
                pref_pages,
                "ask_edit",
                return_value=adapter_row("Ethernet 2", 4500),
            ):
                first.edit_listen()
            self.assertEqual(first.rows[0].port, 4500)
            first.remove_listen()
            self.assertEqual(len(first.rows), 1)
            second = pref_pages.MCPBridgePreferencesPage()
            second._show_mode = lambda: None
            second.loadSettings()
            self.assertEqual(second.rows[0].host, "127.0.0.1")
            second.rows = [adapter_row("Ethernet 2", 4000)]
            second.saveSettings()
            third = pref_pages.MCPBridgePreferencesPage()
            third._show_mode = lambda: None
            third.loadSettings()
        self.assertEqual(third.rows[0].adapter, "Ethernet 2")
        self.assertEqual(third.rows[0].port, 4000)

    def test_host_name_line_matches_agreed_words(self):
        """The Add and Edit dialog's host name note is the agreed sentence."""
        self.assertEqual(
            HOST_NAME_LINE,
            "A host name should resolve to one of the listen addresses, "
            "and a generated self-signed certificate includes it.",
        )

    def test_edit_refuses_what_add_refuses_and_a_duplicate(self):
        """Save refuses every Add refusal plus a duplicate of another row."""
        rows = [
            address_row("127.0.0.1", 39280),
            adapter_row("Ethernet 2", 39280),
            name_row("workshop-pc"),
            address_row("192.168.1.20", 39280),
        ]
        choices = ["Ethernet 2", "Loopback"]
        for kind, val, port, substr in (
            (KIND_ADDRESS, "0.0.0.0", "39280", "wildcard"),
            (KIND_ADDRESS, "::", "39280", "wildcard"),
            (KIND_ADDRESS, "1.2", "39280", "can't be read"),
            (KIND_ADDRESS, "2001:db8:::1", "39280", "can't be read"),
            (KIND_ADDRESS, "workshop-pc", "39280", "not a name"),
            (KIND_ADDRESS, "myhost:39280", "39280", "not a name"),
            (KIND_ADDRESS, "", "39280", "Enter an IP address"),
            (KIND_ADDRESS, "10.0.0.1", "0", "port"),
            (KIND_NAME, "", "39280", "Enter a host name."),
            (KIND_NAME, "my pc", "39280", "without a space"),
            (KIND_NAME, "a,b", "39280", "comma"),
            (KIND_NAME, "a;b", "39280", "semicolon"),
            (KIND_ADAPTER, "", "39280", "Choose an adapter."),
            (KIND_ADAPTER, "VPN", "39280", "not an adapter"),
        ):
            self.assertIn(
                substr,
                edit_refusal(kind, val, port, choices, rows, 3),
            )
        # Changing row 3 (192.168.1.20:39280) to match row 0, 1 or 2 is
        # refused as a duplicate.
        self.assertEqual(
            edit_refusal(
                KIND_ADDRESS, "127.0.0.1", "39280", choices, rows, 3
            ),
            DUPLICATE_ROW_REFUSAL,
        )
        self.assertEqual(
            edit_refusal(
                KIND_ADAPTER, "Ethernet 2", "39280", choices, rows, 3
            ),
            DUPLICATE_ROW_REFUSAL,
        )
        self.assertEqual(
            edit_refusal(
                KIND_NAME, "WORKSHOP-PC", "39280", choices, rows, 3
            ),
            DUPLICATE_ROW_REFUSAL,
        )
        # Saving row 3 unchanged (or with a new port) is allowed.
        self.assertEqual(
            edit_refusal(
                KIND_ADDRESS, "192.168.1.20", "39280", choices, rows, 3
            ),
            "",
        )
        self.assertEqual(
            edit_refusal(
                KIND_ADDRESS, "192.168.1.20", "4000", choices, rows, 3
            ),
            "",
        )
        self.assertTrue(can_edit(rows[0]))
        self.assertTrue(can_edit(rows[1]))
        self.assertTrue(can_edit(rows[2]))
        self.assertFalse(can_edit(legacy_row("workshop-pc:39280")))
        self.assertFalse(can_edit(legacy_row("10.0.0.5")))
        replaced = replace_row(rows, 1, adapter_row("Ethernet 2", 4100))
        self.assertEqual(replaced[1].port, 4100)
        self.assertEqual(replaced[0].host, "127.0.0.1")
        self.assertEqual(replaced[2].host, "workshop-pc")

    def test_edit_dialog_ways_through(self):
        """Open filled, Save, Cancel, refusals, changed kind, and Old entry."""
        import sys
        import types
        from unittest import mock

        opened = []
        on_exec = []

        class Signal:
            def __init__(self):
                self._slots = []

            def connect(self, slot):
                self._slots.append(slot)

            def emit(self, *args):
                for slot in list(self._slots):
                    slot(*args)

        class Widget:
            def __init__(self, parent=None):
                self.parent = parent
                self.visible = True
                self._props = {}

            def setVisible(self, vis):
                self.visible = bool(vis)

            def property(self, name):
                return self._props.get(name)

            def setProperty(self, name, val):
                self._props[name] = val

            def setFixedSize(self, *_args):
                pass

            def setFixedHeight(self, *_args):
                pass

            def setSizePolicy(self, *_args):
                pass

        class Dialog(Widget):
            Accepted = 1
            Rejected = 0

            def __init__(self, parent=None):
                super().__init__(parent)
                self.title = ""
                self.obj_name = ""
                self.result = Dialog.Rejected
                self.widgets = []
                opened.append(self)

            def setWindowTitle(self, title):
                self.title = title

            def setObjectName(self, name):
                self.obj_name = name

            def done(self, code):
                self.result = code
                self._closed = True

            def reject(self):
                self.done(Dialog.Rejected)

            def exec(self):
                self._closed = False
                if on_exec:
                    step = on_exec.pop(0)
                    step(self)
                return self.result

        class ComboBox(Widget):
            def __init__(self, parent=None):
                super().__init__(parent)
                self.items = []
                self.index = 0
                self.currentIndexChanged = Signal()
                if isinstance(parent, Dialog):
                    parent.kind_box = self
                elif parent is not None and isinstance(
                    parent.parent, Dialog
                ):
                    parent.parent.adapter_box = self

            def addItem(self, label, data=None):
                self.items.append((label, data))

            def setCurrentIndex(self, idx):
                self.index = idx
                self.currentIndexChanged.emit(idx)

            def currentData(self):
                if 0 <= self.index < len(self.items):
                    return self.items[self.index][1]
                return None

        class LineEdit(Widget):
            def __init__(self, parent=None):
                super().__init__(parent)
                self._text = ""
                self.selected_all = False
                if parent is not None and isinstance(
                    parent.parent, Dialog
                ):
                    dlg = parent.parent
                    if not hasattr(dlg, "address_box"):
                        dlg.address_box = self
                    else:
                        dlg.name_box = self

            def setText(self, text):
                self._text = text

            def text(self):
                return self._text

            def selectAll(self):
                self.selected_all = True

        class SpinBox(Widget):
            def __init__(self, parent=None):
                super().__init__(parent)
                self._val = 0
                if isinstance(parent, Dialog):
                    parent.port_box = self

            def setRange(self, _low, _high):
                pass

            def setValue(self, val):
                self._val = int(val)

            def value(self):
                return self._val

        class Label(Widget):
            def __init__(self, text="", parent=None):
                super().__init__(parent)
                self._text = text
                if isinstance(parent, Dialog):
                    parent.widgets.append(self)

            def setText(self, text):
                self._text = text

            def text(self):
                return self._text

            def setWordWrap(self, _wrap):
                pass

        class Button(Widget):
            def __init__(self, label):
                super().__init__()
                self.label = label
                self.clicked = Signal()

        class ButtonBox(Widget):
            Cancel = 1
            AcceptRole = 2

            def __init__(self, _std, parent=None):
                super().__init__(parent)
                self.rejected = Signal()
                self.accept_button = None
                if isinstance(parent, Dialog):
                    parent.button_box = self

            def addButton(self, label, _role):
                self.accept_button = Button(label)
                return self.accept_button

        class BoxLayout:
            def __init__(self, _parent=None):
                pass

            def setContentsMargins(self, *_args):
                pass

            def addWidget(self, _widget):
                pass

            def addLayout(self, _layout):
                pass

        class FormLayout:
            def addRow(self, *_args):
                pass

        widgets = types.ModuleType("PySide.QtWidgets")
        widgets.QDialog = Dialog
        widgets.QComboBox = ComboBox
        widgets.QLineEdit = LineEdit
        widgets.QSpinBox = SpinBox
        widgets.QLabel = Label
        widgets.QDialogButtonBox = ButtonBox
        widgets.QVBoxLayout = BoxLayout
        widgets.QFormLayout = FormLayout
        widgets.QWidget = Widget
        widgets.QTableWidget = Widget
        widgets.QPushButton = Button
        widgets.QToolButton = Button
        widgets.QSizePolicy = types.SimpleNamespace(Fixed=0)
        widgets.QTableWidgetItem = Widget
        pyside = types.ModuleType("PySide")

        from freecad.mcp_bridge import pref_pages

        choices = [
            ("Ethernet 2 (192.168.1.20)", "Ethernet 2"),
            ("Loopback (127.0.0.1)", "Loopback"),
        ]
        with mock.patch.dict(
            sys.modules,
            {"PySide": pyside, "PySide.QtWidgets": widgets},
        ):
            rows = [
                address_row("127.0.0.1", 39280),
                adapter_row("Loopback", 4100),
                name_row("studio.example"),
                legacy_row("old-host:39280"),
            ]

            # 1. Open filled for an adapter row, change port, Save replaces.
            def save_adapter(dlg):
                self.assertEqual(dlg.title, "MCP Bridge: Edit row")
                self.assertEqual(
                    dlg.button_box.accept_button.label, "Save"
                )
                self.assertEqual(dlg.kind_box.currentData(), KIND_ADAPTER)
                self.assertEqual(
                    dlg.adapter_box.currentData(), "Loopback"
                )
                self.assertEqual(dlg.port_box.value(), 4100)
                dlg.port_box.setValue(4200)
                dlg.button_box.accept_button.clicked.emit()

            on_exec.append(save_adapter)
            edited = pref_pages.ask_edit(
                None, choices, "my-pc", rows, 1
            )
            self.assertEqual(edited.kind, KIND_ADAPTER)
            self.assertEqual(edited.adapter, "Loopback")
            self.assertEqual(edited.port, 4200)

            # 2. Open filled for a fixed address row, Cancel leaves None.
            def cancel_address(dlg):
                self.assertEqual(dlg.kind_box.currentData(), KIND_ADDRESS)
                self.assertEqual(dlg.address_box.text(), "127.0.0.1")
                self.assertEqual(dlg.port_box.value(), 39280)
                dlg.address_box.setText("192.168.1.99")
                dlg.button_box.rejected.emit()

            on_exec.append(cancel_address)
            self.assertIsNone(
                pref_pages.ask_edit(None, choices, "my-pc", rows, 0)
            )

            # 3. Open filled for a host name row; test refusals and duplicate,
            # then change kind to a fixed address and Save.
            def edit_name_and_change_kind(dlg):
                self.assertEqual(dlg.kind_box.currentData(), KIND_NAME)
                self.assertEqual(dlg.name_box.text(), "studio.example")
                self.assertTrue(dlg.name_box.selected_all)
                explain = dlg.widgets[2]
                error = dlg.widgets[3]
                self.assertEqual(explain.text(), HOST_NAME_LINE)
                # Empty name refused.
                dlg.name_box.setText("")
                dlg.button_box.accept_button.clicked.emit()
                self.assertEqual(error.text(), "Enter a host name.")
                self.assertFalse(dlg._closed)
                # Change kind to fixed IP address (index 1).
                dlg.kind_box.setCurrentIndex(1)
                self.assertEqual(error.text(), "")
                # Wildcard refused.
                dlg.address_box.setText("0.0.0.0")
                dlg.port_box.setValue(39280)
                dlg.button_box.accept_button.clicked.emit()
                self.assertIn("wildcard", error.text())
                self.assertFalse(dlg._closed)
                # Duplicate of row 0 (127.0.0.1:39280) refused.
                dlg.address_box.setText("127.0.0.1")
                dlg.port_box.setValue(39280)
                dlg.button_box.accept_button.clicked.emit()
                self.assertEqual(error.text(), DUPLICATE_ROW_REFUSAL)
                self.assertFalse(dlg._closed)
                # Valid new fixed address succeeds.
                dlg.address_box.setText("192.168.1.50")
                dlg.port_box.setValue(4300)
                dlg.button_box.accept_button.clicked.emit()
                self.assertTrue(dlg._closed)

            on_exec.append(edit_name_and_change_kind)
            changed = pref_pages.ask_edit(
                None, choices, "my-pc", rows, 2
            )
            self.assertEqual(changed.kind, KIND_ADDRESS)
            self.assertEqual(changed.host, "192.168.1.50")
            self.assertEqual(changed.port, 4300)

            # 4. An "Old entry" row does not open the dialog at all.
            opened.clear()
            self.assertIsNone(
                pref_pages.ask_edit(None, choices, "my-pc", rows, 3)
            )
            self.assertEqual(opened, [])

            # 5. Page edit_listen: Old entry does not open; Cancel on the
            # page drops an edit; double-click and Edit button both route
            # to edit_listen.
            class Form:
                def findChild(self, _kind, _name=""):
                    return None

                def findChildren(self, _kind):
                    return []

            with mock.patch.object(
                pref_pages, "load_form", return_value=Form()
            ):
                page = pref_pages.MCPBridgePreferencesPage()
                page._show_mode = lambda: None
                page._choices = lambda: choices
                page._own_name = lambda: "my-pc"
                page._fill = lambda: None
                page.rows = list(rows)
                current_row = {"index": 3}
                page._table = lambda: type(
                    "T",
                    (),
                    {"currentRow": lambda _self: current_row["index"]},
                )()
                # Selecting Old entry (index 3) does not open the dialog.
                page.edit_listen()
                page.edit_listen(3)
                self.assertEqual(opened, [])
                self.assertEqual(page.rows[3].raw, "old-host:39280")
                # Selecting row 0 and clicking Save in the dialog updates
                # page.rows[0] in memory; closing the page without
                # saveSettings() drops the edit on the next open.
                current_row["index"] = 0
                on_exec.append(
                    lambda dlg: (
                        dlg.port_box.setValue(4900),
                        dlg.button_box.accept_button.clicked.emit(),
                    )
                )
                page.edit_listen(False)
                self.assertEqual(page.rows[0].port, 4900)
                reopened = pref_pages.MCPBridgePreferencesPage()
                reopened._show_mode = lambda: None
                reopened.loadSettings()
                self.assertEqual(reopened.rows[0].port, 39280)
