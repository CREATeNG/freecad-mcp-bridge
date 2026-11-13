"""Runtime configuration for the MCP Bridge, read from FreeCAD preferences.

Values live under PREF_GROUP and are read fresh on each access. The listen
addresses and security settings are read at server start. Token checks
hash the candidate and read TokenHash, and a mismatch reads it again.
Timeout and page size are
read per request. Retention is read when a job finishes running.
Status-bar button settings are read when the status-bar button is created or
refreshed.
"""

import datetime
import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import time

try:
    import ssl
except ImportError:
    # Some Python builds ship without the _ssl extension module, as
    # reported for FreeCAD's official snap (#67). Only TLS needs it, so the
    # addon still loads, and a start that needs TLS says why it can't.
    ssl = None

import FreeCAD

from freecad.mcp_bridge import token_file
from freecad.mcp_bridge.constants import (
    DEFAULT_LISTEN_ADDRESS,
    DEFAULT_LISTEN_HOST,
    DEFAULT_MAX_HISTORY_RETENTION_MIN,
    DEFAULT_MAX_PAGE_SIZE_KB,
    DEFAULT_MAX_RESPONSE_TIMEOUT_S,
    DEFAULT_PERMISSION_MODE,
    DEFAULT_PORT,
    DEFAULT_SHOW_STATUS_BUTTON,
    DEFAULT_STATUS_BUTTON_POSITION,
    DISPLAY_NAME,
    PERMISSION_MODE_ASK,
    PERMISSION_MODE_UNRESTRICTED,
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
    `ListenAddress` has no entries and the list has never been saved,
    and return the default entry. A saved list, empty or holding only
    adapters or host names, stays as saved."""
    params = _params()
    raw = params.GetString("ListenAddress", "")
    legacy_port = params.GetInt("Port", DEFAULT_PORT)
    default_entry = f"{DEFAULT_LISTEN_HOST}:{legacy_port}"
    saved = getattr(params, "GetBool", lambda *_args: False)(
        "ListenListSaved", False
    )
    if (
        not saved
        and not any(re.split(r"[\s,;]+", (raw or "").strip()))
        and legacy_port != DEFAULT_PORT
    ):
        if hasattr(params, "RemInt"):
            params.RemInt("Port")
        if hasattr(params, "SetString"):
            params.SetString("ListenAddress", default_entry)
    return default_entry


def listen_addresses() -> list[str]:
    """Fixed `address:port` entries saved for the HTTP server.

    An adapter's address is resolved at start, not stored here.
    With nothing saved, the list is the default entry.
    """
    rows = saved_listen_rows()
    from freecad.mcp_bridge.listeners import KIND_ADDRESS, format_entry

    entries = []
    for row in rows:
        if row.kind != KIND_ADDRESS:
            continue
        entry = format_entry(row.host, row.port)
        if entry not in entries:
            entries.append(entry)
    if entries:
        return entries
    if _params().GetBool("ListenListSaved", False):
        return []
    return [migrate_legacy_port()]


def saved_listen_rows():
    """The listen list: fixed addresses, adapters and host names."""
    from freecad.mcp_bridge.listeners import rows_from_store

    migrate_legacy_port()
    params = _params()
    return rows_from_store(
        params.GetString("ListenAddress", ""),
        params.GetString("ListenAdapters", ""),
        params.GetString("HostNames", ""),
        bool(getattr(params, "GetBool", lambda *_args: False)(
            "ListenListSaved", False
        )),
    )


def save_listen_rows(rows) -> None:
    """Store the listen list. An empty list stays empty."""
    from freecad.mcp_bridge.listeners import (
        dump_adapters,
        dump_addresses,
        dump_names,
    )

    params = _params()
    # First, so an observer that runs on the writes below already
    # finds the list saved and migrates no legacy Port into it.
    params.SetBool("ListenListSaved", True)
    params.SetString("ListenAddress", dump_addresses(rows))
    params.SetString("ListenAdapters", dump_adapters(rows))
    params.SetString("HostNames", dump_names(rows))


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
    """SHA-256 hex of the bearer token, in the TokenHash preference.

    That entry is the one 0.1.27 already used for the hash. The token
    itself is not stored.
    """
    return _params().GetString("TokenHash", "").strip()


def hash_token(token: str) -> str:
    """Return the SHA-256 hex digest of `token`."""
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def store_token(token: str) -> str:
    """Keep only the hash of `token`."""
    digest = hash_token(token)
    _params().SetString("TokenHash", digest)
    return digest


def generate_and_store_token() -> str:
    """Generate a bearer token, store its hash, and return it once."""
    token = secrets.token_urlsafe(32)
    store_token(token)
    return token


def set_pasted_token(token: str) -> str:
    """Store a pasted bearer token. Return its hash.

    Any length is accepted when the text is an RFC 6750 token.
    """
    text = (token or "").strip()
    if not token_file.token_is_bearer(text):
        raise ValueError(token_file.TOKEN_REFUSAL)
    store_token(text)
    return hash_token(text)


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
    """True when `candidate` hashes to the stored hash.

    A mismatch reads the preference again before refusing, so a hash
    changed while the bridge is running is taken up. The comparison
    is constant time. `expected_hash` is accepted for older callers
    and ignored.
    """
    del expected_hash
    digest = hash_token(candidate) if candidate else ""
    if _tokens_match(digest, token_hash()):
        return True
    return _tokens_match(digest, token_hash())


def unrestricted() -> bool:
    """Whether the permission mode is Unrestricted. Ask is the default.

    In Ask mode FreeCAD shows an agent's code before it runs. In
    Unrestricted mode the code runs without being shown.
    """
    mode = _params().GetInt("PermissionMode", DEFAULT_PERMISSION_MODE)
    return mode == PERMISSION_MODE_UNRESTRICTED


def set_unrestricted(enabled: bool) -> None:
    """Set the permission mode: Unrestricted when true, Ask when false."""
    mode = PERMISSION_MODE_UNRESTRICTED if enabled else PERMISSION_MODE_ASK
    _params().SetInt("PermissionMode", mode)


def tls_cert_file() -> str:
    """Path to the TLS certificate file for HTTPS."""
    return _params().GetString("TlsCertFile", "").strip()


def tls_key_file() -> str:
    """Path to the TLS private key file for HTTPS."""
    return _params().GetString("TlsKeyFile", "").strip()


def _entry_host(address: str) -> str:
    """Extract the host part from `address` (host or address:port)."""
    raw = (address or "").strip()
    if not raw:
        return ""
    try:
        host, _ = parse_listen_entry(raw)
        return host
    except ValueError:
        return (
            raw[1:-1]
            if raw.startswith("[") and raw.endswith("]")
            else raw
        )


def _normalize_host(raw: str) -> str:
    """Canonical lowercase host or IP string for certificate matching."""
    host = _entry_host(raw)
    if not host:
        return ""
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return host.lower()


def _decode_cert(cert_file: str) -> dict | None:
    """Return the decoded certificate dict for `cert_file`, or `None` when
    `ssl._ssl._test_decode_cert` is unavailable or raises."""
    cert = (cert_file or "").strip()
    if not cert:
        return None
    decode = getattr(getattr(ssl, "_ssl", None), "_test_decode_cert", None)
    if not callable(decode):
        return None
    try:
        decoded = decode(cert)
    except Exception:
        return None
    return decoded if isinstance(decoded, dict) else None


def _certificate_hosts_from_decoded(decoded: dict) -> set[str]:
    """Extract normalized SAN (`DNS`/`IP Address`) or fallback `commonName`
    hosts from `decoded`."""
    hosts = set()
    for kind, value in decoded.get("subjectAltName", ()):
        if kind in ("DNS", "IP Address") and value:
            norm = _normalize_host(value)
            if norm:
                hosts.add(norm)
    if not hosts:
        for rdn in decoded.get("subject", ()):
            for key, value in rdn:
                if key == "commonName" and value:
                    norm = _normalize_host(value)
                    if norm:
                        hosts.add(norm)
    return hosts


def _certificate_not_yet_valid_date(decoded: dict) -> str | None:
    """Return the UTC `YYYY-MM-DD` start date when `decoded` is not yet
    valid, or `None` when already valid or when `notBefore` cannot be
    parsed."""
    not_before = decoded.get("notBefore")
    if not isinstance(not_before, str) or not not_before.strip():
        return None
    try:
        starts_at = ssl.cert_time_to_seconds(not_before)
    except (ValueError, OverflowError, OSError):
        return None
    if time.time() < starts_at:
        dt = datetime.datetime.fromtimestamp(
            starts_at, tz=datetime.timezone.utc
        )
        return dt.strftime("%Y-%m-%d")
    return None


def _certificate_expired_date(decoded: dict) -> str | None:
    """Return the UTC `YYYY-MM-DD` expiry date when `decoded` has expired,
    or `None` when still valid or when `notAfter` cannot be parsed."""
    not_after = decoded.get("notAfter")
    if not isinstance(not_after, str) or not not_after.strip():
        return None
    try:
        expires_at = ssl.cert_time_to_seconds(not_after)
    except (ValueError, OverflowError, OSError):
        return None
    if time.time() >= expires_at:
        dt = datetime.datetime.fromtimestamp(
            expires_at, tz=datetime.timezone.utc
        )
        return dt.strftime("%Y-%m-%d")
    return None


def certificate_expiry_date(cert_file: str) -> str | None:
    """Return the UTC `YYYY-MM-DD` expiry date of `cert_file`, or `None`
    when `notAfter` cannot be read."""
    decoded = _decode_cert(cert_file)
    if decoded is None:
        return None
    not_after = decoded.get("notAfter")
    if not isinstance(not_after, str) or not not_after.strip():
        return None
    try:
        expires_at = ssl.cert_time_to_seconds(not_after)
    except (ValueError, OverflowError, OSError):
        return None
    dt = datetime.datetime.fromtimestamp(
        expires_at, tz=datetime.timezone.utc
    )
    return dt.strftime("%Y-%m-%d")


def host_names() -> list[str]:
    """Host names accepted on the open listeners. No name opens a listener."""
    raw = _params().GetString("HostNames", "")
    names: list[str] = []
    seen: set[str] = set()
    for item in re.split(r"[\s,;]+", (raw or "").strip()):
        key = item.lower()
        if item and key not in seen:
            seen.add(key)
            names.append(item)
    return names


def host_name_accepted(name: str) -> bool:
    """True when `name` is one of the Host names."""
    key = (name or "").strip().lower()
    if not key:
        return False
    return any(item.lower() == key for item in host_names())


def certificate_target_hosts(
    entries: list[str] | tuple[str, ...],
    extra_names: list[str] | tuple[str, ...] | None = None,
) -> list[str]:
    """Return `127.0.0.1`, `localhost`, every address in `entries`,
    loopback included, then each name in `extra_names`, in order."""
    hosts: list[str] = ["127.0.0.1", "localhost"]
    seen: set[str] = set(hosts)
    for entry in entries:
        host, _ = parse_listen_entry(entry)
        try:
            norm = str(ipaddress.ip_address(host))
        except ValueError:
            norm = host.lower()
        if norm not in seen:
            seen.add(norm)
            hosts.append(norm)
    for name in extra_names or ():
        text = (name or "").strip()
        if not text:
            continue
        try:
            norm = str(ipaddress.ip_address(text))
        except ValueError:
            norm = text.lower()
        if norm not in seen:
            seen.add(norm)
            hosts.append(norm)
    return hosts


def certificate_hosts(cert_file: str) -> set[str]:
    """Names and IP addresses covered by the PEM certificate `cert_file`."""
    decoded = _decode_cert(cert_file)
    if decoded is None:
        return set()
    return _certificate_hosts_from_decoded(decoded)


def certificate_names_a_host(decoded: dict) -> bool:
    """True when `decoded` has a DNS name, not only IP addresses."""
    saw_ip = False
    for kind, value in decoded.get("subjectAltName", ()):
        if (
            kind == "DNS"
            and value
            and str(value).casefold() != "localhost"
        ):
            return True
        if kind == "IP Address" and value:
            saw_ip = True
    if saw_ip:
        return False
    for rdn in decoded.get("subject", ()):
        for key, value in rdn:
            if key == "commonName" and value:
                if str(value).casefold() == "localhost":
                    continue
                try:
                    ipaddress.ip_address(str(value))
                except ValueError:
                    return True
    return False


def listener_coverage_warnings(
    cert_file: str, entries: list[str]
) -> list[str]:
    """One warning per listener address missing from a nameless certificate.

    A certificate that names a host is left to the client's own check.
    """
    decoded = _decode_cert(cert_file)
    if decoded is None or certificate_names_a_host(decoded):
        return []
    covered = _certificate_hosts_from_decoded(decoded)
    warnings = []
    for entry in entries:
        host = _entry_host(entry)
        if certificate_covers_host(covered, host):
            continue
        warnings.append(
            f"{host} is not in the TLS certificate, and the "
            "certificate holds no host name. An MCP client "
            "that verifies the certificate will refuse to "
            f"connect to {host}."
        )
    return warnings


def certificate_covers_host(
    cert_hosts: set[str] | list[str] | tuple[str, ...], host: str
) -> bool:
    """True when `host` (or `address:port`) is covered by `cert_hosts`."""
    norm = _normalize_host(host)
    if not norm or not cert_hosts:
        return False
    normalized = {_normalize_host(item) for item in cert_hosts if item}
    if norm in normalized:
        return True
    try:
        ipaddress.ip_address(norm)
        return False
    except ValueError:
        pass
    for pattern in normalized:
        if pattern.startswith("*.") and "." in norm:
            suffix = pattern[1:]
            prefix = norm[: -len(suffix)]
            if norm.endswith(suffix) and prefix and "." not in prefix:
                return True
    return False


NO_TLS_MESSAGE = (
    "TLS isn't available: this FreeCAD's Python has no ssl module, so a "
    "start that needs TLS can't happen here. Loopback listen addresses "
    "still work over HTTP, as long as Certificate (PEM) and Private key "
    "(PEM) aren't both set. Listen addresses and both fields are in Edit → "
    "Preferences → MCP Bridge."
)


def tls_available() -> bool:
    """Whether this Python has the ssl module that TLS needs."""
    return ssl is not None


def build_ssl_context(
    cert_file: str,
    key_file: str,
    addresses: list[str] | None = None,
) -> "ssl.SSLContext":
    """Build a server SSLContext from `cert_file` and `key_file`.

    `cert_file` must be valid now: not before `notBefore`, and not
    past `notAfter`. A listener whose address the certificate does
    not cover is not a reason to refuse. `addresses` is accepted
    for older callers and is not checked. Without the ssl module it
    refuses with NO_TLS_MESSAGE.
    """
    if ssl is None:
        raise ValueError(NO_TLS_MESSAGE)
    cert = (cert_file or "").strip()
    key = (key_file or "").strip()
    if not cert or not key:
        raise ValueError(
            "An address beyond loopback requires TLS, which needs a "
            "certificate file and a private key file. Select or "
            "generate both in Edit → Preferences → MCP Bridge."
        )
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(
            certfile=cert, keyfile=key, password=lambda: ""
        )
    except (OSError, ssl.SSLError) as exc:
        raise ValueError(
            f"Could not load TLS certificate or private key ({exc}). "
            "Select or generate the TLS files in Edit → Preferences → "
            "MCP Bridge."
        ) from exc
    decoded = _decode_cert(cert)
    if decoded is None:
        FreeCAD.Console.PrintWarning(
            f"[{DISPLAY_NAME}] WARNING: could not inspect the TLS "
            "certificate for expiry or address coverage. Continuing "
            "without those checks. Connect by the listed address, "
            "not by a name.\n"
        )
        return context
    not_before_date = _certificate_not_yet_valid_date(decoded)
    if not_before_date is not None:
        raise ValueError(
            f"The TLS certificate is not valid until {not_before_date}. "
            "Select or generate a certificate in Edit → Preferences → "
            "MCP Bridge."
        )
    expired_date = _certificate_expired_date(decoded)
    if expired_date is not None:
        raise ValueError(
            f"The TLS certificate expired on {expired_date}. "
            "Select or generate a certificate in Edit → Preferences → "
            "MCP Bridge."
        )
    del addresses
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
