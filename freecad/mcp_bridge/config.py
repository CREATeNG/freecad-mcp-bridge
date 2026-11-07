"""Runtime configuration for the MCP Bridge, read from FreeCAD preferences.

Values live under PREF_GROUP and are read fresh on each access. The listen
addresses and port are read at server start. Timeout and page size are read
per request. Retention is read when a job finishes running. Status-bar
button settings are read when the status-bar button is created or refreshed.
"""

import ipaddress
import json
import re

import FreeCAD

from freecad.mcp_bridge.constants import (
    DEFAULT_LISTEN_ADDRESS,
    DEFAULT_MAX_HISTORY_RETENTION_MIN,
    DEFAULT_MAX_PAGE_SIZE_KB,
    DEFAULT_MAX_RESPONSE_TIMEOUT_S,
    DEFAULT_PORT,
    DEFAULT_SHOW_STATUS_BUTTON,
    DEFAULT_STATUS_BUTTON_POSITION,
    PREF_GROUP,
)


def _params():
    return FreeCAD.ParamGet(PREF_GROUP)


def is_loopback_address(address: str) -> bool:
    """True when `address` is localhost or a loopback IP address."""
    host = (address or "").strip()
    if host.lower() == "localhost":
        return True
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def parse_listen_addresses(raw: str) -> list[str]:
    """Split a whitespace-, comma-, or semicolon-separated address list."""
    addresses = []
    for item in re.split(r"[\s,;]+", (raw or "").strip()):
        if item and item not in addresses:
            addresses.append(item)
    return addresses or [DEFAULT_LISTEN_ADDRESS]


def listen_addresses() -> list[str]:
    """Configured listen addresses for the HTTP server."""
    raw = _params().GetString("ListenAddress", DEFAULT_LISTEN_ADDRESS)
    return parse_listen_addresses(raw)


def accepted_listen_addresses() -> list[str]:
    """Non-loopback addresses whose risk dialog the user has accepted."""
    raw = _params().GetString("AcceptedListenAddresses", "")
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(parsed, list):
        return []
    return [
        item.strip()
        for item in parsed
        if isinstance(item, str) and item.strip()
    ]


def is_address_accepted(address: str) -> bool:
    """True if `address` is loopback or has been accepted by the user."""
    addr = (address or "").strip()
    if is_loopback_address(addr):
        return True
    return addr in accepted_listen_addresses()


def accept_address(address: str) -> None:
    """Record that the user accepted the risk dialog for `address`."""
    addr = (address or "").strip()
    if not addr or is_loopback_address(addr):
        return
    accepted = accepted_listen_addresses()
    if addr not in accepted:
        accepted.append(addr)
        _params().SetString(
            "AcceptedListenAddresses", json.dumps(accepted)
        )


def port() -> int:
    """TCP port for the HTTP server."""
    return _params().GetInt("Port", DEFAULT_PORT)


def max_response_timeout_ms() -> int:
    """Max time a tool call waits for output before returning has_more=True."""
    seconds = _params().GetInt(
        "MaxResponseTimeoutSeconds", DEFAULT_MAX_RESPONSE_TIMEOUT_S
    )
    return seconds * 1000


def max_page_size_chars() -> int:
    """Max serialized tool-result length of one page, in characters."""
    kb = _params().GetInt("MaxPageSize", DEFAULT_MAX_PAGE_SIZE_KB)
    return kb * 1024


def max_history_retention_ms() -> int:
    """Retention interval in ms, sampled when a job finishes."""
    minutes = _params().GetInt(
        "MaxHistoryRetentionMinutes", DEFAULT_MAX_HISTORY_RETENTION_MIN
    )
    return minutes * 60 * 1000


def show_status_button() -> bool:
    """Whether the status-bar On/Off button is shown."""
    return _params().GetBool(
        "ShowStatusButton", DEFAULT_SHOW_STATUS_BUTTON
    )


def status_button_position() -> int:
    """Where the status-bar button sits (0 = right, 1 = left)."""
    return _params().GetInt(
        "StatusButtonPosition", DEFAULT_STATUS_BUTTON_POSITION
    )
