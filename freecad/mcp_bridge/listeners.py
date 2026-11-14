"""The listen list: adapter rows, fixed addresses, and host names.

A fixed address is stored in ListenAddress as `address:port`, the
form it has had. A host name is stored in HostNames, one a line.
An adapter is stored in ListenAdapters as the port, a tab, then
the adapter's name. The name is everything after that tab, so it
may hold spaces, brackets and an asterisk, and the line cannot be
read as an address.
"""

from __future__ import annotations

import ipaddress
import re

from freecad.mcp_bridge.constants import (
    DEFAULT_LISTEN_ADDRESS,
    DEFAULT_PORT,
)

_SPLIT = re.compile(r"[\s,;]+")


def split_entries(raw: str) -> list[str]:
    """Entries in `raw`, in order, without blanks."""
    return [item for item in _SPLIT.split((raw or "").strip()) if item]


def parse_names(raw: str) -> list[str]:
    """Host names in `raw`, in order, without duplicates."""
    names = []
    seen: set[str] = set()
    for item in split_entries(raw):
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        names.append(item)
    return names


def format_entry(address: str, port: int) -> str:
    """`address:port`, with brackets around an IPv6 address."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return f"{address}:{port}"
    if ip.version == 6:
        return f"[{ip}]:{port}"
    return f"{ip}:{port}"

KIND_ADDRESS = "address"
KIND_ADAPTER = "adapter"
KIND_NAME = "name"
KIND_LEGACY = "legacy"

KIND_FIXED_LABEL = "A fixed IP address"
KIND_ADAPTER_LABEL = "An adapter of this machine"
KIND_NAME_LABEL = "A host name"
ROW_FIXED = "Fixed IP address"
ROW_NAME = "Host name"

HOST_NAME_LINE = (
    "A host name should resolve to one of the listen addresses, "
    "and a generated self-signed certificate includes it."
)
DUPLICATE_ROW_REFUSAL = "That row is already in the list."


class ListenRow:
    """One row of the listen list."""

    def __init__(
        self,
        kind: str,
        host: str = "",
        port: int = 0,
        adapter: str = "",
        raw: str = "",
    ):
        self.kind = kind
        self.host = host
        self.port = port
        self.adapter = adapter
        self.raw = raw

    def identity(self) -> tuple:
        """The parts that make two rows the same row."""
        if self.kind == KIND_ADAPTER:
            return (KIND_ADAPTER, self.adapter.casefold(), self.port)
        if self.kind == KIND_NAME:
            return (KIND_NAME, self.host.casefold())
        if self.kind == KIND_ADDRESS:
            return (KIND_ADDRESS, _ip_key(self.host), self.port)
        return (KIND_LEGACY, self.raw.casefold())


def address_row(host: str, port: int) -> ListenRow:
    """A fixed IP address and a port."""
    return ListenRow(KIND_ADDRESS, host=_ip_text(host), port=port)


def adapter_row(name: str, port: int) -> ListenRow:
    """An adapter of this machine and a port."""
    return ListenRow(KIND_ADAPTER, port=port, adapter=name.strip())


def name_row(name: str) -> ListenRow:
    """A host name. It opens no listener."""
    return ListenRow(KIND_NAME, host=name.strip())


def legacy_row(raw: str) -> ListenRow:
    """A saved name with a port. It is not a listen address."""
    return ListenRow(KIND_LEGACY, raw=raw.strip())


def default_rows() -> list[ListenRow]:
    """What a new user sees: 127.0.0.1:39280, a fixed address."""
    return [address_row("127.0.0.1", DEFAULT_PORT)]


def rows_from_store(
    addresses: str,
    adapters: str,
    names: str,
    list_saved: bool,
) -> list[ListenRow]:
    """The rows saved in the three preferences.

    With nothing saved, the list is the default row. A saved empty
    list stays empty.
    """
    fixed = _fixed_rows(addresses)
    adapter_rows = _adapter_rows(adapters)
    name_rows = [name_row(item) for item in parse_names(names)]
    if (
        not list_saved
        and not fixed
        and not adapter_rows
        and not split_entries(addresses)
    ):
        fixed = default_rows()
    return [*fixed, *adapter_rows, *name_rows]


def dump_addresses(rows: list[ListenRow]) -> str:
    """ListenAddress: fixed addresses, and a legacy row's old text."""
    lines = []
    for row in rows:
        if row.kind == KIND_ADDRESS:
            lines.append(format_entry(row.host, row.port))
        elif row.kind == KIND_LEGACY and row.raw:
            lines.append(row.raw)
    return "\n".join(lines)


def dump_adapters(rows: list[ListenRow]) -> str:
    """ListenAdapters: `port`, a tab, then the adapter's name."""
    lines = []
    for row in rows:
        if row.kind == KIND_ADAPTER and row.adapter:
            lines.append(f"{row.port}\t{row.adapter}")
    return "\n".join(lines)


def dump_names(rows: list[ListenRow]) -> str:
    """HostNames: one host name a line."""
    return "\n".join(
        row.host for row in rows if row.kind == KIND_NAME and row.host
    )


def cells(
    row: ListenRow, present: str | None
) -> tuple[str, str, str]:
    """`(what, port, kind)` for the three columns.

    `present` is the adapter's IPv4 address now, or None when the
    adapter is gone. The port is its own column, so an adapter name
    is never parsed back out of the line.
    """
    if row.kind == KIND_ADAPTER:
        kind = "Adapter"
        if present:
            kind = f"Adapter, {present}"
        return (row.adapter, str(row.port), kind)
    if row.kind == KIND_NAME:
        return (row.host, "", ROW_NAME)
    if row.kind == KIND_ADDRESS:
        return (row.host, str(row.port), ROW_FIXED)
    return (row.raw, "", "Old entry")


def row_note(
    row: ListenRow,
    machine: set[str],
    adapter_names: set[str],
    ipv4_of: dict[str, str] | None = None,
) -> str:
    """The note for `row`, or '' when the row needs none."""
    if row.kind == KIND_ADDRESS and _unspecified(row.host):
        shown = "::" if ":" in row.host else "0.0.0.0"
        return (
            f"{shown} is a wildcard, so the bridge opens "
            "no listener on it."
        )
    if row.kind == KIND_ADDRESS and not _on_machine(row.host, machine):
        return (
            f"{row.host} is not an address of this machine, "
            "so the bridge opens no listener on it."
        )
    if row.kind == KIND_ADAPTER and row.adapter not in adapter_names:
        return (
            f"{row.adapter} is down or is not an adapter of "
            "this machine, so the bridge opens no listener "
            "on it."
        )
    if (
        row.kind == KIND_ADAPTER
        and ipv4_of is not None
        and row.adapter not in ipv4_of
    ):
        return (
            f"{row.adapter} has no usable IPv4 address, so the "
            "bridge opens no listener on it."
        )
    if row.kind == KIND_LEGACY:
        if _bare_ip(row.raw):
            return (
                f"{row.raw} has no port. Remove this row, and "
                f"add {row.raw} as a fixed IP address with its "
                "port."
            )
        return (
            f"{row.raw} is a name with a port, which is not "
            "a listen address. Remove this row, add an "
            "adapter or a fixed IP address, and add the "
            "name as a host name."
        )
    return ""


def adapter_names(records: list[tuple[str, str]]) -> set[str]:
    """The adapter names in `records`."""
    return {
        (name or "").strip()
        for name, _address in records
        if (name or "").strip()
    }


def adapter_ipv4(records: list[tuple[str, str]]) -> dict[str, str]:
    """Each adapter's first non-link-local IPv4 address.

    `records` is `(adapter name, address)`. Link-local addresses
    are left out. An adapter with none is absent.
    """
    found: dict[str, str] = {}
    for name, address in records:
        adapter = (name or "").strip()
        text = (address or "").split("%", 1)[0].strip()
        if not adapter or not text or adapter in found:
            continue
        try:
            ip = ipaddress.ip_address(text)
        except ValueError:
            continue
        if ip.version != 4 or ip.is_link_local:
            continue
        found[adapter] = str(ip)
    return found


def adapter_choices(
    records: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """`(label, adapter name)` for the Add dialog's pick list.

    The label is `name (address)`. Link-local addresses are left out.
    """
    choices = []
    for name, address in adapter_ipv4(records).items():
        choices.append((f"{name} ({address})", name))
    return choices


def add_refusal(
    kind: str,
    value: str,
    port_text: str,
    choices: list[str],
) -> str:
    """Why Add refuses, or '' when the row may be added.

    A row already in the list is not a refusal: adding it changes
    nothing, and the caller checks that itself.
    """
    if kind == KIND_NAME:
        name = (value or "").strip()
        if not name:
            return "Enter a host name."
        if any(ch.isspace() or ch in ",;" for ch in name):
            return (
                "Enter one host name, without a space, comma "
                "or semicolon."
            )
        return ""
    port_error = _port_refusal(port_text)
    if port_error:
        return port_error
    if kind == KIND_ADAPTER:
        name = (value or "").strip()
        if not name:
            return "Choose an adapter."
        if name not in choices:
            return f"{name} is not an adapter of this machine."
        return ""
    return _address_refusal(value)


def make_row(kind: str, value: str, port_text: str) -> ListenRow:
    """The row Add would insert. `port_text` is ignored for a name."""
    if kind == KIND_NAME:
        return name_row(value)
    port = int(port_text)
    if kind == KIND_ADAPTER:
        return adapter_row(value, port)
    return address_row(value, port)


def can_edit(row: ListenRow) -> bool:
    """True when `row` can be opened in the edit dialog."""
    return row.kind in (KIND_ADAPTER, KIND_ADDRESS, KIND_NAME)


def edit_refusal(
    kind: str,
    value: str,
    port_text: str,
    choices: list[str],
    rows: list[ListenRow],
    index: int,
) -> str:
    """Why Save refuses when editing row `index`, or ''."""
    reason = add_refusal(kind, value, port_text, choices)
    if reason:
        return reason
    row = make_row(kind, value, port_text)
    key = row.identity()
    for number, item in enumerate(rows):
        if number != index and item.identity() == key:
            return DUPLICATE_ROW_REFUSAL
    return ""


def already_listed(rows: list[ListenRow], row: ListenRow) -> bool:
    """True when `row` is already in `rows`."""
    key = row.identity()
    return any(item.identity() == key for item in rows)


def with_row(rows: list[ListenRow], row: ListenRow) -> list[ListenRow]:
    """`rows` plus `row`, or `rows` unchanged when it is already there."""
    if already_listed(rows, row):
        return list(rows)
    return [*rows, row]


def replace_row(
    rows: list[ListenRow], index: int, row: ListenRow
) -> list[ListenRow]:
    """`rows` with `row` in place of the row at `index`."""
    if index < 0 or index >= len(rows):
        return list(rows)
    updated = list(rows)
    updated[index] = row
    return updated


def without_index(rows: list[ListenRow], index: int) -> list[ListenRow]:
    """`rows` without the row at `index`. The last row may go."""
    if index < 0 or index >= len(rows):
        return list(rows)
    return [row for number, row in enumerate(rows) if number != index]


def bind_entries(
    rows: list[ListenRow],
    ipv4_of: dict[str, str],
    machine: set[str] | None = None,
) -> list[str]:
    """`address:port` for every listener the bridge can open.

    An adapter listens on the IPv4 address it has now. A host name,
    a legacy row, a wildcard, and a fixed address this machine does
    not have open nothing. `machine` is that check; without it, a
    fixed address is kept.
    """
    entries = []
    for row in rows:
        if row.kind == KIND_ADDRESS:
            if _unspecified(row.host):
                continue
            if machine is not None and not _on_machine(
                row.host, machine
            ):
                continue
            entry = format_entry(row.host, row.port)
        elif row.kind == KIND_ADAPTER:
            present = ipv4_of.get(row.adapter, "")
            if not present:
                continue
            entry = format_entry(present, row.port)
        else:
            continue
        if entry not in entries:
            entries.append(entry)
    return entries


def host_already_accepted(stored: list[str], host: str) -> bool:
    """True when `host` was accepted, with or without a port.

    The list may still hold today's `address:port` entries. Either
    form accepts that address.
    """
    target = _ip_key(host)
    if not target:
        return False
    for item in stored:
        text = (item or "").strip()
        if not text:
            continue
        if _ip_key(text) == target:
            return True
        pair = _split_host_port(text)
        if pair is not None and _ip_key(pair[0]) == target:
            return True
    return False


def risk_line(row: ListenRow, present: str) -> str:
    """The line the risk dialog shows for `row`."""
    if row.kind == KIND_ADAPTER and present:
        return f"{row.adapter} ({present}:{row.port})"
    if row.kind == KIND_ADDRESS:
        return format_entry(row.host, row.port)
    return ""


def start_notes(
    rows: list[ListenRow],
    ipv4_of: dict[str, str],
    machine: set[str],
    adapter_names: set[str],
) -> list[str]:
    """What the start says, the same notes the page shows."""
    notes = []
    for row in rows:
        if row.kind == KIND_ADAPTER and row.adapter in adapter_names:
            if row.adapter not in ipv4_of:
                notes.append(
                    f"{row.adapter} has no usable IPv4 address, so the "
                    "bridge opens no listener on it."
                )
                continue
        note = row_note(row, machine, adapter_names, ipv4_of)
        if note and note not in notes:
            notes.append(note)
    return notes


def _fixed_rows(raw: str) -> list[ListenRow]:
    rows = []
    for item in split_entries(raw):
        pair = _split_host_port(item)
        if pair is None:
            rows.append(legacy_row(item))
            continue
        host, port = pair
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            rows.append(legacy_row(item))
            continue
        rows.append(ListenRow(KIND_ADDRESS, host=str(ip), port=port))
    return rows


def _adapter_rows(raw: str) -> list[ListenRow]:
    rows = []
    for line in (raw or "").splitlines():
        if "\t" not in line:
            continue
        port_text, name = line.split("\t", 1)
        name = name.strip()
        if (
            not name
            or not port_text.strip().isdigit()
            or not (1 <= int(port_text) <= 65535)
        ):
            continue
        rows.append(adapter_row(name, int(port_text)))
    return rows


def _split_host_port(entry: str) -> tuple[str, int] | None:
    """`(host, port)` from `address:port` or `[ipv6]:port`.

    Unlike `parse_listen_entry`, a wildcard is still split, so a
    saved one can be shown. A name with a port is split too.
    """
    item = (entry or "").strip()
    if not item:
        return None
    if item.startswith("["):
        if "]:" not in item:
            return None
        host, port_part = item[1:].rsplit("]:", 1)
    else:
        if item.count(":") != 1:
            return None
        host, port_part = item.rsplit(":", 1)
    if (
        not host
        or not port_part.isdigit()
        or not (1 <= int(port_part) <= 65535)
    ):
        return None
    return host, int(port_part)


def _unspecified(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_unspecified
    except ValueError:
        return False


def _ip_text(host: str) -> str:
    return str(ipaddress.ip_address(host.strip()))


def _ip_key(host: str) -> str:
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return host.casefold()


def _on_machine(host: str, machine: set[str]) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if ip.is_loopback:
        return True
    return str(ip) in machine


def _bare_ip(text: str) -> bool:
    """True when `text` is an IP address and not address:port."""
    try:
        ipaddress.ip_address((text or "").strip())
    except ValueError:
        return False
    return True


def _name_with_port(text: str) -> bool:
    """True when `text` is a name, a colon, and a port."""
    if text.count(":") != 1:
        return False
    host, port = text.rsplit(":", 1)
    return bool(host) and any(ch.isalpha() for ch in host) and port.isdigit()


def _port_refusal(port_text: str) -> str:
    text = (port_text or "").strip()
    if not text.isdigit() or not (1 <= int(text) <= 65535):
        return "Use a port from 1 to 65535."
    return ""


def _address_refusal(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return (
            "Enter an IP address. A name is added as a host name."
        )
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        if _name_with_port(text) or (
            ":" not in text and any(ch.isalpha() for ch in text)
        ):
            return (
                "A fixed IP address has to be an address, not a "
                "name. Add the name as a host name."
            )
        return (
            "That address can't be read. Use an address such as "
            "192.168.1.20."
        )
    if ip.is_unspecified:
        shown = "0.0.0.0" if ip.version == 4 else "::"
        return (
            f"{shown} is a wildcard. Add an adapter, or a specific "
            "address of this machine."
        )
    return ""


def difference_text(
    page: list[str], listening: list[str] | None
) -> str:
    """How the page's listeners differ from a bridge that is on.

    An empty page stays empty. No default address is put in.
    `listening` is None when the bridge is off.
    """
    if listening is None:
        return ""
    if sorted(page) == sorted(list(listening)):
        return ""
    page_s = ", ".join(page) if page else "nothing"
    listen_s = ", ".join(listening) if listening else "nothing"
    return (
        f"The bridge is listening on {listen_s}. "
        f"The Addresses list has {page_s}. "
        "Click OK, then turn the bridge off and on again, "
        "to apply the change."
    )


def own_host_name() -> str:
    """This machine's own host name."""
    import socket

    return socket.gethostname().strip()


def default_entry() -> str:
    """The new user's one row, as ListenAddress stores it."""
    return DEFAULT_LISTEN_ADDRESS
