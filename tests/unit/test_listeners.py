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
    acceptance_key,
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
        self.assertTrue(notes)
        self.assertIn("not a listen address", notes[0])

    def test_an_accepted_address_ignores_the_port(self):
        """A changed port does not ask again. The key is the address."""
        row = adapter_row("Ethernet 2", 4000)
        self.assertEqual(acceptance_key(row, "192.168.1.20"), "192.168.1.20")
        self.assertIn(
            "Ethernet 2 (192.168.1.20:4000)",
            risk_line(row, "192.168.1.20"),
        )
        stored = ["192.168.1.20:39280"]
        self.assertTrue(host_already_accepted(stored, "192.168.1.20"))
        self.assertFalse(host_already_accepted(stored, "10.0.0.1"))
