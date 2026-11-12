"""Where Generate self-signed certificate writes its files.

The certificate tool writes `tls-cert.pem` and `tls-key.pem` in
`token_file.token_dir()`. The addon makes no certificate or key of
its own: `cert_tools` runs a tool on this machine.
"""

from __future__ import annotations

from pathlib import Path

from freecad.mcp_bridge import token_file

CERT_FILE_NAME = "tls-cert.pem"
KEY_FILE_NAME = "tls-key.pem"


def cert_path(directory: Path | None = None) -> Path:
    """Path to `tls-cert.pem` in the bridge's folder."""
    base = directory if directory is not None else token_file.token_dir()
    return base / CERT_FILE_NAME


def key_path(directory: Path | None = None) -> Path:
    """Path to `tls-key.pem` in the bridge's folder."""
    base = directory if directory is not None else token_file.token_dir()
    return base / KEY_FILE_NAME
