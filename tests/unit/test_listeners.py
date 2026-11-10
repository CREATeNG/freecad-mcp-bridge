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
    cells,
    default_rows,
    dump_adapters,
    host_already_accepted,
    name_row,
    risk_line,
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

    def test_a_fixed_address_off_this_machine_blocks_before_the_dialog(self):
        """The start names that row and does not treat it as a listener."""
        from freecad.mcp_bridge.listeners import blocking_fixed_notes

        row = address_row("10.9.9.9", 39280)
        notes = blocking_fixed_notes([row], {"192.168.1.20"})
        self.assertEqual(
            notes,
            [
                "10.9.9.9 is not an address of this machine. "
                "The bridge will not start with it."
            ],
        )
        self.assertEqual(bind_entries([row], {}), ["10.9.9.9:39280"])

    def test_an_adapter_with_no_ipv4_is_skipped_and_marked(self):
        """An up adapter with no IPv4 is not a listener, and the row says so."""
        from freecad.mcp_bridge.listeners import row_note

        row = adapter_row("Ethernet 2", 39280)
        note = row_note(row, set(), {"Ethernet 2"}, {})
        self.assertEqual(
            note,
            "Ethernet 2 has no IPv4 address, so the bridge "
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
        self.assertIn("Listen addresses has nothing", text)
        self.assertNotIn("127.0.0.1:39280. Listen addresses has 127", text)

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
            first = pref_pages.PreferencesPage()
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
            first.remove_listen()
            self.assertEqual(len(first.rows), 1)
            second = pref_pages.PreferencesPage()
            second._show_mode = lambda: None
            second.loadSettings()
            self.assertEqual(second.rows[0].host, "127.0.0.1")
            second.rows = [adapter_row("Ethernet 2", 4000)]
            second.saveSettings()
            third = pref_pages.PreferencesPage()
            third._show_mode = lambda: None
            third.loadSettings()
        self.assertEqual(third.rows[0].adapter, "Ethernet 2")
        self.assertEqual(third.rows[0].port, 4000)
