"""The bearer token and serving certificate files FreeCAD and the extension share.

The folder is ``FREECAD_MCP_TOKEN_DIR`` when that variable is set,
and ``<home>/.freecad-mcp-bridge`` otherwise. The token file is
``token``, and the certificate copy written when the bridge starts
with TLS is ``serving-cert.pem``. The folder is readable by the user
only, and its one allow entry is inheritable, so a file created in it
is user-only too. Each write sets that same user-only access on the
file itself.
"""

from __future__ import annotations

import os
import stat
import sys
import tempfile
import time
from pathlib import Path

DIR_NAME = ".freecad-mcp-bridge"
FILE_NAME = "token"
SERVING_CERT_FILE_NAME = "serving-cert.pem"
MIN_TOKEN_LENGTH = 32
_REPLACE_ATTEMPTS = 5


def token_dir() -> Path:
    """Folder that holds the token file.

    ``FREECAD_MCP_TOKEN_DIR`` overrides the home folder when it is set.
    """
    override = os.environ.get("FREECAD_MCP_TOKEN_DIR", "").strip()
    if override:
        return Path(override)
    return Path.home() / DIR_NAME


def token_path() -> Path:
    """Path of the token file."""
    return token_dir() / FILE_NAME


def serving_cert_path() -> Path:
    """Path of the serving certificate copy (`serving-cert.pem`)."""
    return token_dir() / SERVING_CERT_FILE_NAME


def read_token() -> str:
    """Return the token in the file, or '' when it cannot be read."""
    try:
        return token_path().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def write_token(token: str) -> None:
    """Write `token` by replacing the file from a temporary file.

    The temporary file is in the same folder, so it inherits that
    folder's user-only entry. A reader never sees an empty token file.
    """
    directory = token_dir()
    directory.mkdir(parents=True, exist_ok=True)
    _user_only(directory, is_dir=True)
    descriptor, name = tempfile.mkstemp(
        prefix=".token-", suffix=".tmp", dir=directory
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(token.strip() + "\n")
        _user_only(temporary, is_dir=False)
        _replace_file(temporary, token_path())
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def write_serving_cert(cert_file: str | Path) -> None:
    """Write a copy of `cert_file` to `serving-cert.pem` beside the token file."""
    content = Path(cert_file).read_bytes()
    directory = token_dir()
    directory.mkdir(parents=True, exist_ok=True)
    _user_only(directory, is_dir=True)
    descriptor, name = tempfile.mkstemp(
        prefix=".serving-cert-", suffix=".tmp", dir=directory
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
        _user_only(temporary, is_dir=False)
        _replace_file(temporary, serving_cert_path())
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def remove_serving_cert() -> None:
    """Remove `serving-cert.pem` if it exists."""
    serving_cert_path().unlink(missing_ok=True)


def _replace_file(source: Path, dest: Path) -> None:
    """Replace `dest` with `source`. Retry a Windows sharing error."""
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(source, dest)
            return
        except OSError as exc:
            if (
                not _sharing_violation(exc)
                or attempt == _REPLACE_ATTEMPTS - 1
            ):
                raise
            time.sleep(0.05 * (attempt + 1))


def _sharing_violation(exc: OSError) -> bool:
    """True for a Windows sharing or lock error."""
    if not sys.platform.startswith("win"):
        return False
    return getattr(exc, "winerror", None) in (32, 33)


def _user_only(path: Path, *, is_dir: bool) -> None:
    """Leave `path` readable and writable by the current user only."""
    if sys.platform.startswith("win"):
        _windows_user_only(path, is_dir=is_dir)
        return
    mode = (
        stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
        if is_dir
        else stat.S_IRUSR | stat.S_IWUSR
    )
    os.chmod(path, mode)


def _windows_user_only(path: Path, *, is_dir: bool) -> None:
    """Set a protected ACL with one inheritable allow entry for this user."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

    token_query = 0x0008
    token_user = 1
    object_inherit = 0x1
    container_inherit = 0x2
    file_all_access = 0x1F01FF
    dacl_security_information = 0x4
    protected_dacl = 0x80000000
    se_file_object = 1
    acl_revision = 2

    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.GetLengthSid.argtypes = [wintypes.LPVOID]
    advapi32.GetLengthSid.restype = wintypes.DWORD
    advapi32.InitializeAcl.argtypes = [
        wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD
    ]
    advapi32.InitializeAcl.restype = wintypes.BOOL
    advapi32.AddAccessAllowedAceEx.argtypes = [
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
    ]
    advapi32.AddAccessAllowedAceEx.restype = wintypes.BOOL
    advapi32.SetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.LPVOID,
    ]
    advapi32.SetNamedSecurityInfoW.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), token_query, ctypes.byref(handle)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        needed = wintypes.DWORD(0)
        advapi32.GetTokenInformation(
            handle, token_user, None, 0, ctypes.byref(needed)
        )
        buffer = ctypes.create_string_buffer(needed.value)
        if not advapi32.GetTokenInformation(
            handle, token_user, buffer, needed, ctypes.byref(needed)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel32.CloseHandle(handle)

    sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
    sid_len = advapi32.GetLengthSid(sid)
    ace_size = 8 + sid_len
    if ace_size % 4:
        ace_size += 4 - (ace_size % 4)
    acl_size = 8 + ace_size
    acl = ctypes.create_string_buffer(acl_size)
    if not advapi32.InitializeAcl(acl, acl_size, acl_revision):
        raise ctypes.WinError(ctypes.get_last_error())
    flags = 0
    if is_dir:
        flags = object_inherit | container_inherit
    if not advapi32.AddAccessAllowedAceEx(
        acl, acl_revision, flags, file_all_access, sid
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    info = dacl_security_information | protected_dacl
    result = advapi32.SetNamedSecurityInfoW(
        str(path), se_file_object, info, None, None, acl, None
    )
    if result != 0:
        raise ctypes.WinError(result)
