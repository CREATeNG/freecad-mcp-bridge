"""Certificate tools on this machine, for the Generate certificate button.

``find_tools`` lists what can make a certificate here. ``generate``
runs the chosen one. The key file is left readable by the user only,
through the token file's helper, whichever tool wrote it.

A tool id is ``openssl`` (on PATH), ``git-openssl`` (Git for Windows,
at its usual install paths), or ``powershell`` (Windows PowerShell
5.1, CertificateRequest). A key type is ``rsa-4096`` or
``ecdsa-p256``.
"""

from __future__ import annotations

import ipaddress
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from freecad.mcp_bridge import token_file
from freecad.mcp_bridge.constants import (
    CERT_VALID_DAYS_DEFAULT,
    CERT_VALID_DAYS_MAX,
    CERT_VALID_DAYS_MIN,
)

KEY_RSA = "rsa-4096"
KEY_ECDSA = "ecdsa-p256"
_KEY_TYPES = (KEY_RSA, KEY_ECDSA)

_GIT_OPENSSL = (
    Path(r"C:\Program Files\Git\usr\bin\openssl.exe"),
    Path(r"C:\Program Files\Git\mingw64\bin\openssl.exe"),
    Path(r"C:\Program Files (x86)\Git\usr\bin\openssl.exe"),
)


class CertToolError(Exception):
    """A certificate tool is missing or the chosen tool failed."""


def find_tools() -> list[dict[str, str]]:
    """Certificate tools on this machine, PATH openssl first.

    Each item has ``id``, passed to ``generate``, and ``label``,
    for the button to show.
    """
    found: list[dict[str, str]] = []
    on_path = shutil.which("openssl")
    if on_path:
        found.append({"id": "openssl", "label": "openssl"})
    for candidate in _GIT_OPENSSL:
        if not candidate.is_file():
            continue
        if on_path and Path(on_path).resolve() == candidate.resolve():
            continue
        found.append({
            "id": "git-openssl",
            "label": "openssl (Git for Windows)",
            "path": str(candidate),
        })
        break
    if _powershell_exe() is not None:
        found.append({
            "id": "powershell",
            "label": "Windows PowerShell",
        })
    return found


def generate(
    tool: str,
    key_type: str,
    hosts: list[str],
    cert_path: str,
    key_path: str,
    days: int = CERT_VALID_DAYS_DEFAULT,
) -> None:
    """Write a self-signed pair for ``hosts`` with ``tool``.

    Names become dNSName entries and addresses become iPAddress
    entries. The certificate is valid for ``days`` days from now,
    1 to 3650, and cannot sign other certificates. The key file is
    readable by the user only.
    """
    if (
        isinstance(days, bool)
        or not isinstance(days, int)
        or not CERT_VALID_DAYS_MIN <= days <= CERT_VALID_DAYS_MAX
    ):
        raise CertToolError(
            f"Valid for must be {CERT_VALID_DAYS_MIN} to "
            f"{CERT_VALID_DAYS_MAX} days."
        )
    if key_type not in _KEY_TYPES:
        raise CertToolError(
            f"Unknown key type {key_type}. Use rsa-4096 or ecdsa-p256."
        )
    cleaned = _clean_hosts(hosts)
    cert = Path(cert_path)
    key = Path(key_path)
    _protect_created_directory(cert.parent)
    _protect_created_directory(key.parent)
    # The tool writes a temporary pair. The real files are replaced
    # only after that succeeds, so a failure leaves them as they were.
    cert_tmp = _temporary_output(cert)
    key_tmp = _temporary_output(key)
    try:
        token_file._user_only(key_tmp, is_dir=False)
        _generate_with(
            tool, key_type, cleaned, cert_tmp, key_tmp, days
        )
        token_file._replace_file(cert_tmp, cert)
        token_file._replace_file(key_tmp, key)
    except Exception:
        _discard(cert_tmp)
        _discard(key_tmp)
        raise


def _temporary_output(dest: Path) -> Path:
    descriptor, name = tempfile.mkstemp(
        prefix=f".{dest.name}.", suffix=".tmp", dir=dest.parent
    )
    os.close(descriptor)
    return Path(name)


def _discard(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _generate_with(
    tool: str,
    key_type: str,
    hosts: list[str],
    cert: Path,
    key: Path,
    days: int = CERT_VALID_DAYS_DEFAULT,
) -> None:
    if tool == "openssl":
        binary = shutil.which("openssl")
        if not binary:
            raise CertToolError("openssl is not on PATH.")
        _openssl_generate(binary, key_type, hosts, cert, key, days)
    elif tool == "git-openssl":
        binary = _git_openssl()
        if binary is None:
            raise CertToolError("Git for Windows openssl was not found.")
        _openssl_generate(
            str(binary), key_type, hosts, cert, key, days
        )
    elif tool == "powershell":
        exe = _powershell_exe()
        if exe is None:
            raise CertToolError("Windows PowerShell was not found.")
        _powershell_generate(exe, key_type, hosts, cert, key, days)
    else:
        raise CertToolError(f"Unknown certificate tool {tool}.")


def _protect_created_directory(path: Path) -> None:
    """Create `path` when it is missing, and protect only what this created."""
    if path.exists():
        return
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        if current.parent == current:
            break
        current = current.parent
    path.mkdir(parents=True, exist_ok=True)
    for created in reversed(missing):
        if created.is_dir():
            token_file._user_only(created, is_dir=True)


def _clean_hosts(hosts: list[str]) -> list[str]:
    cleaned = []
    for raw in hosts:
        host = (raw or "").strip()
        if host.startswith("[") and host.endswith("]"):
            host = host[1:-1]
        if host and host not in cleaned:
            cleaned.append(host)
    if not cleaned:
        raise CertToolError("At least one host is required.")
    return cleaned


def _powershell_exe() -> Path | None:
    if not sys.platform.startswith("win"):
        return None
    windir = os.environ.get("SystemRoot", r"C:\Windows")
    exe = (
        Path(windir)
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    return exe if exe.is_file() else None


def _git_openssl() -> Path | None:
    for candidate in _GIT_OPENSSL:
        if candidate.is_file():
            return candidate
    return None


def _script_path() -> Path:
    return (
        Path(__file__).resolve().parent
        / "Resources"
        / "generate_cert.ps1"
    )


def _run(command: list[str]) -> None:
    # FreeCAD on Windows is a windowed program. Without this flag a
    # console window flashes for the length of openssl or PowerShell.
    flags = {}
    if sys.platform.startswith("win"):
        flags["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            **flags,
        )
    except OSError as exc:
        raise CertToolError(str(exc)) from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise CertToolError(detail or "The certificate tool failed.")


def _openssl_config(hosts: list[str]) -> str:
    lines = [
        "[req]",
        "distinguished_name = req_dn",
        "x509_extensions = v3_req",
        "prompt = no",
        "[req_dn]",
        f"CN = {hosts[0]}",
        "[v3_req]",
        "basicConstraints = critical,CA:FALSE",
        "keyUsage = critical,digitalSignature,keyEncipherment",
        "extendedKeyUsage = serverAuth",
        "subjectAltName = @alt_names",
        "[alt_names]",
    ]
    dns_n = 0
    ip_n = 0
    for host in hosts:
        try:
            ipaddress.ip_address(host)
        except ValueError:
            dns_n += 1
            lines.append(f"DNS.{dns_n} = {host}")
        else:
            ip_n += 1
            lines.append(f"IP.{ip_n} = {host}")
    return "\n".join(lines) + "\n"


def _openssl_generate(
    binary: str,
    key_type: str,
    hosts: list[str],
    cert: Path,
    key: Path,
    days: int = CERT_VALID_DAYS_DEFAULT,
) -> None:
    with tempfile.TemporaryDirectory() as folder:
        config = Path(folder) / "cert.cnf"
        config.write_text(
            _openssl_config(hosts), encoding="utf-8", newline="\n"
        )
        if key_type == KEY_ECDSA:
            # ecparam, then req -key, is accepted by OpenSSL and by
            # LibreSSL (macOS /usr/bin/openssl). -newkey ec -pkeyopt
            # may not be.
            _run([
                binary,
                "ecparam",
                "-name",
                "prime256v1",
                "-genkey",
                "-noout",
                "-out",
                str(key),
            ])
            command = [
                binary,
                "req",
                "-new",
                "-x509",
                "-key",
                str(key),
                "-out",
                str(cert),
                "-days",
                str(days),
                "-config",
                str(config),
            ]
        else:
            command = [
                binary,
                "req",
                "-x509",
                "-newkey",
                "rsa:4096",
                "-nodes",
                "-keyout",
                str(key),
                "-out",
                str(cert),
                "-days",
                str(days),
                "-config",
                str(config),
            ]
        _run(command)


def _powershell_generate(
    exe: Path,
    key_type: str,
    hosts: list[str],
    cert: Path,
    key: Path,
    days: int = CERT_VALID_DAYS_DEFAULT,
) -> None:
    kind = "RSA" if key_type == KEY_RSA else "ECDSA"
    _run(
        [
            str(exe),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(_script_path()),
            "-KeyType",
            kind,
            "-Hosts",
            ";".join(hosts),
            "-CertPath",
            str(cert),
            "-KeyPath",
            str(key),
            "-ValidDays",
            str(days),
        ]
    )
