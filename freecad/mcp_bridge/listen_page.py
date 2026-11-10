"""Rows, ticks and marks for the Listen addresses page.

A ticked address is one entry in the list. A host name is not a
listener: it is accepted on the addresses that are already open.
"""

from __future__ import annotations

import ipaddress
import re
import socket

from freecad.mcp_bridge.config import parse_listen_entry
from freecad.mcp_bridge.constants import DEFAULT_LISTEN_HOST, DEFAULT_PORT

_SPLIT = re.compile(r"[\s,;]+")


def split_entries(raw: str) -> list[str]:
    """Entries in `raw`, in order, without blanks."""
    return [item for item in _SPLIT.split((raw or "").strip()) if item]


def join_entries(entries: list[str]) -> str:
    """One entry per line."""
    return "\n".join(entries)


def hosts_equal(left: str, right: str) -> bool:
    """True when `left` and `right` are the same address or name."""
    a = (left or "").strip()
    b = (right or "").strip()
    if not a or not b:
        return False
    try:
        return ipaddress.ip_address(a) == ipaddress.ip_address(b)
    except ValueError:
        return a.lower() == b.lower()


def format_entry(address: str, port: int) -> str:
    """`address:port`, with brackets around an IPv6 address."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return f"{address}:{port}"
    if ip.version == 6:
        return f"[{ip}]:{port}"
    return f"{ip}:{port}"


def port_for_new_row(raw: str) -> int:
    """The port of the 127.0.0.1 entry, or 39280 when there is none."""
    for item in split_entries(raw):
        try:
            host, port = parse_listen_entry(item)
        except ValueError:
            continue
        if host == DEFAULT_LISTEN_HOST:
            return port
    return DEFAULT_PORT


def effective_entries(raw: str) -> list[str]:
    """Entries the page acts on. An empty box is the default."""
    items = split_entries(raw)
    if items:
        return items
    return [format_entry(DEFAULT_LISTEN_HOST, DEFAULT_PORT)]


def row_is_ticked(raw: str, address: str) -> bool:
    """True when any effective entry is for `address`."""
    for item in effective_entries(raw):
        try:
            host, _port = parse_listen_entry(item)
        except ValueError:
            continue
        if hosts_equal(host, address):
            return True
    return False


def with_address(raw: str, address: str) -> str:
    """Add `address` at the row port when it is not already listed.

    An empty box is the default entry, so a new tick keeps it.
    """
    base = (
        effective_entries(raw)
        if not split_entries(raw)
        else split_entries(raw)
    )
    if row_is_ticked(raw, address):
        return join_entries(base)
    entries = list(base)
    entries.append(format_entry(address, port_for_new_row(raw)))
    return join_entries(entries)


def without_address(raw: str, address: str) -> str:
    """Remove every entry for `address`, whatever port it uses."""
    kept = []
    for item in split_entries(raw):
        try:
            host, _port = parse_listen_entry(item)
        except ValueError:
            kept.append(item)
            continue
        if hosts_equal(host, address):
            continue
        kept.append(item)
    return join_entries(kept)


def address_rows(
    records: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """`(label, address)` for up interfaces.

    `records` is `(interface name, address)`. IPv4 comes first.
    Link-local IPv6 is left out. `::1` stays. One row per address.
    The label is `<address> (<interface name>)`.
    """
    ipv4: list[tuple[str, str]] = []
    ipv6: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name, address in records:
        text = (address or "").split("%", 1)[0].strip()
        if not text:
            continue
        try:
            ip = ipaddress.ip_address(text)
        except ValueError:
            continue
        if ip.version == 6 and ip.is_link_local:
            continue
        key = str(ip)
        if key in seen:
            continue
        seen.add(key)
        iface = (name or "").strip()
        label = f"{text} ({iface})" if iface else text
        row = (label, text)
        if ip.version == 4:
            ipv4.append(row)
        else:
            ipv6.append(row)
    return ipv4 + ipv6


def machine_addresses(rows: list[tuple[str, str]]) -> set[str]:
    """Normalized addresses from `rows`."""
    found = set()
    for _label, address in rows:
        try:
            found.add(str(ipaddress.ip_address(address)))
        except ValueError:
            continue
    return found


def on_this_machine(host: str, listed: set[str]) -> bool:
    """True when `host` is an address of this machine.

    Every address in `listed` counts, and so does all of 127.0.0.0/8.
    The /8 is not copied into `listed`.
    """
    try:
        ip = ipaddress.ip_address((host or "").strip())
    except ValueError:
        return False
    if ip.version == 4 and ip in ipaddress.ip_network("127.0.0.0/8"):
        return True
    return str(ip) in listed


def entry_marks(raw: str, machine: set[str]) -> list[str]:
    """Lines for entries that will stop a start, and the empty-list note.

    A name in the list is left alone. An empty list is noted, and a
    start with nothing saved still uses 127.0.0.1:39280, because an
    unset list looks the same.
    """
    items = split_entries(raw)
    if not items:
        return [
            "The list is empty, so the bridge starts on 127.0.0.1:39280."
        ]
    marks = []
    for item in items:
        try:
            host, _port = parse_listen_entry(item)
        except ValueError as exc:
            marks.append(str(exc))
            continue
        try:
            ipaddress.ip_address(host)
        except ValueError:
            continue
        if not on_this_machine(host, machine):
            marks.append(
                f"{host} is not an address of this machine. "
                "The bridge will not start with it."
            )
    return marks


def parse_names(raw: str) -> list[str]:
    """Host names in `raw`, in order, without duplicates."""
    names = []
    seen: set[str] = set()
    for item in _SPLIT.split((raw or "").strip()):
        if not item:
            continue
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        names.append(item)
    return names


def name_is_listed(raw: str, name: str) -> bool:
    """True when `name` is one of the host names in `raw`."""
    key = (name or "").strip().lower()
    if not key:
        return False
    return any(item.lower() == key for item in parse_names(raw))


def with_name(raw: str, name: str) -> str:
    """Add `name` if it is not already listed."""
    text = (name or "").strip()
    if not text or name_is_listed(raw, text):
        return "\n".join(parse_names(raw))
    return "\n".join([*parse_names(raw), text])


def without_name(raw: str, name: str) -> str:
    """Remove `name` from the host name list."""
    key = (name or "").strip().lower()
    return "\n".join(
        item for item in parse_names(raw) if item.lower() != key
    )


def host_name_notes(
    names: list[str],
    resolve,
    machine: set[str],
) -> list[str]:
    """A note for each name that does not resolve to this machine.

    The note does not stop a start. `resolve(name)` returns addresses
    or raises OSError.
    """
    notes = []
    for name in names:
        try:
            ipaddress.ip_address(name)
            continue
        except ValueError:
            pass
        try:
            resolved = list(resolve(name) or [])
        except OSError:
            resolved = []
        here = False
        for addr in resolved:
            text = str(addr).split("%", 1)[0]
            try:
                if str(ipaddress.ip_address(text)) in machine:
                    here = True
                    break
            except ValueError:
                continue
        if not here:
            notes.append(
                f"{name} does not resolve to this machine. "
                "The bridge still starts."
            )
    return notes


def own_host_name() -> str:
    """This machine's own host name."""
    return socket.gethostname().strip()


def resolve_name(name: str) -> list[str]:
    """Addresses `name` resolves to, or none when lookup fails."""
    try:
        infos = socket.getaddrinfo(name, None)
    except OSError:
        return []
    found = []
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        addr = str(sockaddr[0]).split("%", 1)[0]
        if addr not in found:
            found.append(addr)
    return found


def difference_text(page_raw: str, listening: list[str] | None) -> str:
    """How the page differs from a bridge that is on.

    `listening` is None when the bridge is off, and then there is
    nothing to say.
    """
    if listening is None:
        return ""
    page = effective_entries(page_raw)
    if _same_entries(page, list(listening)):
        return ""
    page_s = ", ".join(page)
    listen_s = ", ".join(listening) if listening else "nothing"
    return (
        f"The bridge is listening on {listen_s}. "
        f"Listen addresses has {page_s}. "
        "Click OK, then turn the bridge off and on again, "
        "to apply the change."
    )


def _same_entries(left: list[str], right: list[str]) -> bool:
    return sorted(_norm_entry(item) for item in left) == sorted(
        _norm_entry(item) for item in right
    )


def _norm_entry(item: str) -> str:
    try:
        host, port = parse_listen_entry(item)
    except ValueError:
        return (item or "").lower()
    try:
        host = str(ipaddress.ip_address(host))
    except ValueError:
        host = host.lower()
    return f"{host}:{port}"
