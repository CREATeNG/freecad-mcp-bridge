"""Runtime configuration for the MCP Bridge, read from FreeCAD preferences.

Values live under PREF_GROUP and are read fresh on each access. The listen
addresses and security settings are read at server start. Token checks read
the token file, and a mismatch reads it again. Timeout and page size are
read per request. Retention is read when a job finishes running.
Status-bar button settings are read when the status-bar button is created or
refreshed.
"""

import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import ssl

import FreeCAD

from freecad.mcp_bridge import token_file
from freecad.mcp_bridge.constants import (
    DEFAULT_LISTEN_ADDRESS,
    DEFAULT_LISTEN_HOST,
    DEFAULT_MAX_HISTORY_RETENTION_MIN,
    DEFAULT_MAX_PAGE_SIZE_KB,
    DEFAULT_MAX_RESPONSE_TIMEOUT_S,
    DEFAULT_PORT,
    DEFAULT_REQUIRE_TLS,
    DEFAULT_SHOW_STATUS_BUTTON,
    DEFAULT_STATUS_BUTTON_POSITION,
    PREF_GROUP,
)


def _params():
    return FreeCAD.ParamGet(PREF_GROUP)


def parse_listen_entry(entry: str) -> tuple[str, int]:
    """Parse an `address:port` or `[ipv6]:port` entry into `(host, port)`."""
    item = (entry or "").strip()
    if not item:
        raise ValueError("Empty listen address entry. Use address:port.")
    if item.startswith("["):
        if "]:" not in item:
            if item.endswith("]"):
                raise ValueError(
                    f"Listen address '{item}' has no port. Use [ipv6]:port."
                )
            raise ValueError(
                f"Invalid IPv6 listen address '{item}'. Use [ipv6]:port."
            )
        host, port_part = item[1:].rsplit("]:", 1)
        if not host or ":" not in host or "[" in host or "]" in host:
            raise ValueError(
                f"Invalid IPv6 listen address '{item}'. Use [ipv6]:port."
            )
    else:
        if ":" not in item:
            raise ValueError(
                f"Listen address '{item}' has no port. Use address:port."
            )
        if item.count(":") > 1:
            raise ValueError(
                f"IPv6 listen address '{item}' needs brackets around the "
                "address. Use [ipv6]:port."
            )
        host, port_part = item.rsplit(":", 1)
        if not host or "[" in host or "]" in host:
            raise ValueError(
                f"Invalid listen address '{item}'. Use address:port."
            )
    if not port_part:
        raise ValueError(
            f"Listen address '{item}' has no port. Use address:port."
        )
    if not port_part.isdigit() or not (1 <= int(port_part) <= 65535):
        raise ValueError(
            f"Invalid port in listen address '{item}'. Use a port in 1-65535."
        )
    try:
        unspecified = ipaddress.ip_address(host).is_unspecified
    except ValueError:
        unspecified = False
    if unspecified:
        raise ValueError(
            f"Listen address '{item}' is not a specific address of "
            "this machine. Enter a specific address, such as "
            "127.0.0.1 or this machine's own address."
        )
    return host, int(port_part)


def is_unspecified_address(host: str) -> bool:
    """True when `host` is 0.0.0.0, ::, or another any-address form."""
    try:
        return ipaddress.ip_address(host).is_unspecified
    except ValueError:
        return False


def is_loopback_address(address: str) -> bool:
    """True when `address` (host or address:port) is localhost or loopback."""
    raw = (address or "").strip()
    try:
        host, _ = parse_listen_entry(raw)
    except ValueError:
        host = (
            raw[1:-1]
            if raw.startswith("[") and raw.endswith("]")
            else raw
        )
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def parse_listen_addresses(
    raw: str, default_entry: str = DEFAULT_LISTEN_ADDRESS
) -> list[str]:
    """Split a whitespace-, comma-, or semicolon-separated entry list."""
    entries = []
    for item in re.split(r"[\s,;]+", (raw or "").strip()):
        if item and item not in entries:
            entries.append(item)
    return entries or [default_entry]


def migrate_legacy_port() -> str:
    """Migrate a customized legacy `Port` into `ListenAddress` when
    `ListenAddress` has no entries, and return the default entry."""
    params = _params()
    raw = params.GetString("ListenAddress", "")
    legacy_port = params.GetInt("Port", DEFAULT_PORT)
    default_entry = f"{DEFAULT_LISTEN_HOST}:{legacy_port}"
    if (
        not any(re.split(r"[\s,;]+", (raw or "").strip()))
        and legacy_port != DEFAULT_PORT
    ):
        if hasattr(params, "RemInt"):
            params.RemInt("Port")
        if hasattr(params, "SetString"):
            params.SetString("ListenAddress", default_entry)
    return default_entry


def listen_addresses() -> list[str]:
    """Configured `address:port` entries for the HTTP server."""
    raw = _params().GetString("ListenAddress", "")
    if any(re.split(r"[\s,;]+", (raw or "").strip())):
        return parse_listen_addresses(raw)
    return [migrate_legacy_port()]


def accepted_listen_addresses() -> list[str]:
    """Non-loopback entries whose risk dialog the user has accepted."""
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


def start_warning_accepted() -> bool:
    """True after the user has accepted the first-start dialog."""
    return _params().GetBool("StartWarningAccepted", False)


def accept_start_warning() -> None:
    """Remember that the user accepted the first-start dialog."""
    _params().SetBool("StartWarningAccepted", True)


def token_hash() -> str:
    """Legacy preference. The bridge leaves it unread."""
    return _params().GetString("TokenHash", "").strip()


def hash_token(token: str) -> str:
    """Return the SHA-256 hex digest of `token`."""
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def generate_and_store_token() -> str:
    """Generate a new bearer token, write it to the token file, and
    return it once."""
    token = secrets.token_urlsafe(32)
    token_file.write_token(token)
    return token


def _tokens_match(candidate: str, expected: str) -> bool:
    if not candidate or not expected:
        return False
    try:
        return hmac.compare_digest(candidate, expected)
    except ValueError:
        return False


def verify_token(
    candidate: str, expected_hash: str | None = None
) -> bool:
    """True when `candidate` matches the token file.

    A mismatch reads the file again before refusing, so a token
    written while the bridge is running is taken up.
    `expected_hash` is accepted for older callers and ignored.
    """
    del expected_hash
    if _tokens_match(candidate, token_file.read_token()):
        return True
    return _tokens_match(candidate, token_file.read_token())


def require_tls() -> bool:
    """Whether non-loopback entries serve HTTPS.

    The old Apply-to-loopback preference is unread, so loopback stays HTTP.
    """
    return _params().GetBool("RequireTls", DEFAULT_REQUIRE_TLS)


def tls_cert_file() -> str:
    """Path to the TLS certificate file for HTTPS."""
    return _params().GetString("TlsCertFile", "").strip()


def tls_key_file() -> str:
    """Path to the TLS private key file for HTTPS."""
    return _params().GetString("TlsKeyFile", "").strip()


def build_ssl_context(cert_file: str, key_file: str) -> ssl.SSLContext:
    """Build a server SSLContext from `cert_file` and `key_file`."""
    cert = (cert_file or "").strip()
    key = (key_file or "").strip()
    if not cert or not key:
        raise ValueError(
            "TLS is required and needs a certificate file and a private "
            "key file. Select both in Edit → Preferences → MCP Bridge, "
            "or untick Require TLS there."
        )
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(
            certfile=cert, keyfile=key, password=lambda: ""
        )
    except (OSError, ssl.SSLError) as exc:
        raise ValueError(
            f"Could not load TLS certificate or private key ({exc}). "
            "Check the TLS files or untick Require TLS in Edit → "
            "Preferences → MCP Bridge."
        ) from exc
    return context


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
