"""Rows, ticks and marks for Listen addresses, against a made-up list."""

import sys
import unittest


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
                ("Loopback 127.0.0.1", "127.0.0.1"),
                ("Ethernet 192.168.1.20", "192.168.1.20"),
                ("Loopback ::1", "::1"),
                ("Wi-Fi 2001:db8::5", "2001:db8::5"),
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
        added = listen_page.with_address(raw, "192.168.1.20")
        self.assertIn("192.168.1.20:4000", listen_page.split_entries(added))
        self.assertTrue(
            listen_page.row_is_ticked(added, "192.168.1.20")
        )
        no_loop = listen_page.with_address("", "192.168.1.20")
        self.assertEqual(no_loop, f"192.168.1.20:{DEFAULT_PORT}")
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
                "Nothing to listen on. An empty list starts the bridge "
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
        self.assertIn("10.1.1.1 is not an address of this machine.", marks)
        self.assertFalse(any("freecad.local" in line for line in marks))
        self.assertFalse(any("127.0.0.1 is not" in line for line in marks))

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
            "This list is 127.0.0.1:39280, 192.168.1.20:39280. "
            "Click OK, then turn the bridge off and on, to apply it.",
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
            ["127.0.0.1:39280"], ["My-PC", "lan.example"]
        )
        self.assertEqual(
            hosts, ["127.0.0.1", "localhost", "my-pc", "lan.example"]
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
