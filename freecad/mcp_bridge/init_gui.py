import os
import sys

import FreeCAD as App
import FreeCADGui as Gui

from freecad.mcp_bridge import MCP_Bridge, bridge
from freecad.mcp_bridge.listeners import (
    KIND_NAME,
    adapter_ipv4,
    bind_entries,
)
from freecad.mcp_bridge.constants import (
    COMMAND_ID,
    DISPLAY_NAME,
    LOG_PREFIX,
    PREF_GROUP,
    STATUS_BUTTON_LEFT,
    STATUS_BUTTON_OBJECT_NAME,
    TOOLBAR_OBJECT_NAME,
)
from freecad.mcp_bridge.resources import (
    _mod_root,
    icon_path,
    preferences_ui_path,
)

App.MCPBridgeIconPath = icon_path()
App.MCPBridgeCommand = COMMAND_ID

_icons_dir = os.path.join(_mod_root(), "Resources", "Icons")
if os.path.isdir(_icons_dir):
    Gui.addIconPath(_icons_dir.replace("\\", "/"))


def _risk_dialog_text(entries):
    """Return (title, text) for the first-time non-loopback risk dialog."""
    if isinstance(entries, str):
        entries = [entries]
    unaccepted_endpoints = "\n".join(entries)
    title = f"{DISPLAY_NAME}: Listen beyond this machine?"
    body = f"""Listening on these addresses makes this FreeCAD session \
reachable from other machines:
{unaccepted_endpoints}

• Code execution: any client with the bearer token that can reach these \
addresses can run Python code in FreeCAD as your user account.

Accept these risks and start the bridge?"""
    return title, body


def _confirm_non_loopback_risk(entries):
    """Ask the user to accept the risks of listening on `entries`."""
    import FreeCADGui as Gui
    from PySide.QtWidgets import QDialog, QMessageBox

    title, body = _risk_dialog_text(entries)
    box = QMessageBox(
        QMessageBox.Warning,
        title,
        body,
        QMessageBox.NoButton,
        Gui.getMainWindow(),
    )
    QDialog.setWindowTitle(box, title)
    box.setObjectName("MCPBridgeRiskDialog")
    accept_btn = box.addButton(
        "Accept Risks and Start", QMessageBox.AcceptRole
    )
    cancel_btn = box.addButton("Cancel", QMessageBox.RejectRole)
    box.setDefaultButton(cancel_btn)
    box.setEscapeButton(cancel_btn)
    try:
        box.exec()
        return box.clickedButton() is accept_btn
    finally:
        box.setParent(None)
        box.deleteLater()


_COPIED_SHOWN_MS = 1500


def _token_dialog_text(token):
    """Return (title, text) for the one-time bearer token dialog."""
    title = f"{DISPLAY_NAME}: Bearer token"
    body = (
        "This is the bridge's bearer token. It is shown only now: "
        "FreeCAD keeps a hash of it, not the token.\n"
        "\n"
        f"{token}\n"
        "\n"
        "Copy it now. Every MCP client needs it. In Claude Desktop, "
        "paste it into the Bearer token setting of the MCP Bridge for "
        "FreeCAD extension. Other MCP clients send it in the "
        "Authorization header:\n"
        "Authorization: Bearer <token>\n"
        "\n"
        "If you lose it, click Generate new token again. A new token "
        "replaces this one, and every MCP client then needs the new one."
    )
    return title, body


def _show_generated_token(token, parent=None):
    """Display `token` once, with Copy token and Close.

    Copy token copies and leaves the dialog open, and reads "Copied"
    for a moment. Close and Escape end it.
    """
    import FreeCADGui as Gui
    from PySide.QtCore import Qt, QTimer
    from PySide.QtGui import QGuiApplication
    from PySide.QtWidgets import QDialog, QMessageBox

    title, body = _token_dialog_text(token)
    host = parent if parent is not None else Gui.getMainWindow()
    box = QMessageBox(
        QMessageBox.Information,
        title,
        body,
        QMessageBox.NoButton,
        host,
    )
    QDialog.setWindowTitle(box, title)
    box.setObjectName("MCPBridgeTokenDialog")
    box.setTextInteractionFlags(Qt.TextSelectableByMouse)
    copy_btn = box.addButton("Copy token", QMessageBox.ActionRole)
    close_btn = box.addButton("Close", QMessageBox.AcceptRole)
    box.setDefaultButton(close_btn)
    box.setEscapeButton(close_btn)
    copies = []

    def show_copy_token(count):
        if count != len(copies):
            return
        try:
            copy_btn.setText("Copy token")
        except RuntimeError:
            pass

    def copy_token(*_args):
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(token)
        copies.append(None)
        count = len(copies)
        copy_btn.setMinimumWidth(copy_btn.width())
        copy_btn.setText("Copied")
        QTimer.singleShot(
            _COPIED_SHOWN_MS, lambda: show_copy_token(count)
        )

    # A message box closes on a click of any of its buttons. Copy token
    # is taken off that, so it copies and the dialog stays open.
    stays_open = True
    try:
        copy_btn.clicked.disconnect()
    except (RuntimeError, TypeError):
        stays_open = False
    copy_btn.clicked.connect(copy_token)
    try:
        while True:
            box.exec()
            if not stays_open and box.clickedButton() is copy_btn:
                continue
            break
    finally:
        box.setParent(None)
        box.deleteLater()


def _first_start_dialog_text(entries):
    """Return (title, text) for the first start, with or without addresses."""
    title = f"{DISPLAY_NAME}: Start the bridge?"
    permissions = (
        "Connected agents can run Python code in FreeCAD with your "
        "full permissions."
    )
    if not entries:
        body = f"""{permissions}

The bridge listens only after you accept.

Accept and start the bridge?"""
        return title, body
    _risk_title, risk_body = _risk_dialog_text(entries)
    body = f"""{permissions}

{risk_body}"""
    return title, body


def _confirm_first_start(entries):
    """Ask the user to accept the first start. Return True if they do."""
    import FreeCADGui as Gui
    from PySide.QtWidgets import QDialog, QMessageBox

    title, body = _first_start_dialog_text(entries)
    box = QMessageBox(
        QMessageBox.Warning,
        title,
        body,
        QMessageBox.NoButton,
        Gui.getMainWindow(),
    )
    QDialog.setWindowTitle(box, title)
    box.setObjectName("MCPBridgeFirstStartDialog")
    accept_btn = box.addButton("Accept and Start", QMessageBox.AcceptRole)
    cancel_btn = box.addButton("Cancel", QMessageBox.RejectRole)
    box.setDefaultButton(cancel_btn)
    box.setEscapeButton(cancel_btn)
    try:
        box.exec()
        return box.clickedButton() is accept_btn
    finally:
        box.setParent(None)
        box.deleteLater()


def _show_token_message(body, parent=None):
    """Show a bearer-token warning. The title stays MCP Bridge: Bearer token."""
    import FreeCADGui as Gui
    from PySide.QtWidgets import QDialog, QMessageBox

    title = f"{DISPLAY_NAME}: Bearer token"
    host = parent if parent is not None else Gui.getMainWindow()
    box = QMessageBox(
        QMessageBox.Warning,
        title,
        body,
        QMessageBox.NoButton,
        host,
    )
    QDialog.setWindowTitle(box, title)
    box.setObjectName("MCPBridgeTokenMessageDialog")
    ok_btn = box.addButton("OK", QMessageBox.AcceptRole)
    box.setDefaultButton(ok_btn)
    box.setEscapeButton(ok_btn)
    try:
        box.exec()
    finally:
        box.setParent(None)
        box.deleteLater()


def _set_token_dialog_text():
    """Return (title, text) for Set token. The token is not shown back."""
    title = f"{DISPLAY_NAME}: Set token"
    body = (
        "Paste a bearer token. It becomes the bridge's only token, "
        "and every MCP client must then send it. FreeCAD keeps a "
        "hash of it, not the token."
    )
    return title, body


def set_pasted_token_from_dialog(parent=None):
    """Ask for a pasted token. Do not show that token back."""
    import FreeCADGui as Gui
    from PySide.QtWidgets import (
        QDialog,
        QDialogButtonBox,
        QLabel,
        QLineEdit,
        QVBoxLayout,
    )

    from freecad.mcp_bridge import config

    host = parent if parent is not None else Gui.getMainWindow()
    title, body = _set_token_dialog_text()
    dlg = QDialog(host)
    QDialog.setWindowTitle(dlg, title)
    dlg.setObjectName("MCPBridgeSetTokenDialog")
    layout = QVBoxLayout(dlg)
    label = QLabel(body, dlg)
    label.setWordWrap(True)
    layout.addWidget(label)
    edit = QLineEdit(dlg)
    edit.setObjectName("setTokenLineEdit")
    edit.setEchoMode(QLineEdit.Password)
    layout.addWidget(edit)
    buttons = QDialogButtonBox(
        QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dlg
    )
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)
    layout.addWidget(buttons)

    try:
        if dlg.exec() != QDialog.Accepted:
            return None
        text = edit.text().strip()
        try:
            config.set_pasted_token(text)
        except ValueError as exc:
            _show_token_message(str(exc), parent=host)
            return None
        _report_line("Set the bearer token.")
        return text
    finally:
        dlg.setParent(None)
        dlg.deleteLater()


def _report_line(sentence: str) -> None:
    """Print one Report view line. It never includes a token."""
    try:
        import FreeCAD as App
    except Exception:
        return
    from datetime import datetime

    console = getattr(App, "Console", None)
    printer = getattr(console, "PrintMessage", None)
    if printer is None:
        return
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    printer(f"{LOG_PREFIX} {stamp} {sentence}\n")


def _setup_dialog_text(
    need_token: bool, need_tls: bool, need_listen: bool = False
):
    """Return (title, text) for what is missing before a start."""
    title = f"{DISPLAY_NAME}: Set up the bridge"
    lines = [
        "The bridge can't start yet. In Edit → Preferences → "
        "MCP Bridge, it needs:",
    ]
    if need_listen:
        lines.append(
            "• A listen address. Under Listen addresses, click + "
            "and add an adapter or a fixed IP address."
        )
    if need_token:
        lines.append(
            "• An authorization bearer token. Click Generate new "
            "token, or Set token if you already have one from "
            "elsewhere."
        )
    if need_tls:
        lines.append(
            "• A TLS certificate and private key, for the addresses "
            "beyond loopback in Listen addresses. Enter your own "
            "certificate files, or click Generate self-signed "
            "certificate."
        )
    lines.append(
        "Click Open Preferences to go there, then turn the bridge "
        "on again."
    )
    return title, "\n".join(lines)


def _show_setup_dialog(
    need_token: bool, need_tls: bool, need_listen: bool = False
) -> None:
    """Say what is missing. Nothing starts. Open Preferences can
    open the MCP Bridge page."""
    import FreeCADGui as Gui
    from PySide.QtWidgets import QDialog, QMessageBox

    title, body = _setup_dialog_text(need_token, need_tls, need_listen)
    box = QMessageBox(
        QMessageBox.Warning,
        title,
        body,
        QMessageBox.NoButton,
        Gui.getMainWindow(),
    )
    QDialog.setWindowTitle(box, title)
    box.setObjectName("MCPBridgeSetupDialog")
    open_btn = box.addButton(
        "Open Preferences", QMessageBox.ActionRole
    )
    close_btn = box.addButton("Close", QMessageBox.RejectRole)
    box.setDefaultButton(close_btn)
    box.setEscapeButton(close_btn)
    try:
        box.exec()
        if box.clickedButton() is open_btn:
            Gui.showPreferences(DISPLAY_NAME, 0)
    finally:
        box.setParent(None)
        box.deleteLater()


def generate_and_show_token(parent=None):
    """Generate a new bearer token, store its hash, and show it once."""
    from freecad.mcp_bridge import config

    token = config.generate_and_store_token()
    _report_line("Generated a new bearer token.")
    _show_generated_token(token, parent=parent)
    return token


CERT_KEY_TYPES = ("RSA 4096", "ECDSA P-256")
_CERT_KEY_TYPE_IDS = {
    "RSA 4096": "rsa-4096",
    "ECDSA P-256": "ecdsa-p256",
}


def _tool_id(tool):
    """Return the tool identifier expected by `cert_tools.generate()`."""
    if isinstance(tool, dict) and "id" in tool:
        return tool["id"]
    if hasattr(tool, "id"):
        return tool.id
    return str(tool)


def _key_type_id(key_type):
    """Return the key-type identifier expected by `cert_tools.generate()`."""
    return _CERT_KEY_TYPE_IDS.get(key_type, key_type)


def _cert_tool_install_hint(platform_name=None):
    """Return the message for no certificate tool on this machine:
    nothing was made, and where to install one for the platform."""
    plat = sys.platform if platform_name is None else platform_name
    if plat == "win32":
        where = (
            "Install Git for Windows (which includes openssl) or "
            "openssl for Windows."
        )
    elif plat == "darwin":
        where = (
            "Install openssl (for example with Homebrew: "
            "brew install openssl) or Xcode Command Line Tools."
        )
    else:
        where = (
            "Install the openssl package from your system package "
            "manager (for example: apt install openssl or "
            "dnf install openssl)."
        )
    return (
        "No certificate tool was found on this machine, so no "
        f"certificate was made. {where} Then click Generate "
        "self-signed certificate again, or enter your own certificate "
        "files."
    )


def _tool_display_label(tool):
    """Return a human-readable label for a tool returned by `find_tools()`."""
    if isinstance(tool, str):
        return tool
    if isinstance(tool, dict):
        for key in ("label", "name", "description", "kind", "path"):
            val = tool.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        return str(tool)
    for attr in ("label", "name", "description"):
        val = getattr(tool, attr, None)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return str(tool)


def _find_cert_tools():
    """Return `(cert_tools_module, tools)` from `freecad.mcp_bridge.cert_tools`,
    or `(None, [])` when `cert_tools` is unavailable or returns no tools."""
    import importlib

    try:
        cert_tools = importlib.import_module("freecad.mcp_bridge.cert_tools")
    except ImportError:
        return None, []
    find_fn = getattr(cert_tools, "find_tools", None)
    if not callable(find_fn):
        return cert_tools, []
    tools = list(find_fn() or ())
    return cert_tools, tools


def _cert_replace_dialog_text():
    """Return `(title, body)` asking whether to replace an existing TLS
    certificate and private key."""
    return (
        f"{DISPLAY_NAME}: Replace TLS certificate?",
        "Replace the TLS certificate and private key?\n\n"
        "Any MCP client that trusts the current certificate will need the "
        "new certificate file.",
    )


def _cert_generated_dialog_text(cert_file, hosts, expiry_date=""):
    """Return `(title, body)` after generating a TLS certificate and private
    key."""
    addr_lines = "\n".join(f"  {host}" for host in hosts)
    expiry_line = (
        f"{expiry_date} (825 days)" if expiry_date else "825 days"
    )
    body = (
        "Generated a new TLS certificate and private key.\n\n"
        f"Certificate file:\n  {cert_file}\n\n"
        f"Addresses covered:\n{addr_lines}\n\n"
        f"Expires:\n  {expiry_line}\n\n"
    )
    body += (
        "Click OK in Preferences to use it. The Claude Desktop "
        "extension on this machine takes the certificate by itself "
        "at the bridge's next start. Give any other MCP client a "
        "copy of this certificate file, not the private key, to trust."
    )
    return (f"{DISPLAY_NAME}: TLS certificate", body)


def _cert_error_dialog_text(reason):
    """Return `(title, body)` when certificate generation or writing fails."""
    return (
        f"{DISPLAY_NAME}: TLS certificate",
        "Could not generate the TLS certificate "
        f"({str(reason).strip().rstrip('.')}). "
        "Choose another certificate tool, or select your own files.",
    )


def _chooser_file_name(host, object_name):
    """Read `fileName` from a `Gui::PrefFileChooser` on `host`, if present."""
    try:
        return _chooser_file_name_live(host, object_name)
    except RuntimeError:
        return ""


def _chooser_file_name_live(host, object_name):
    """The read, so a deleted chooser is caught by the caller."""
    if host is None:
        return ""
    try:
        from PySide.QtWidgets import QLineEdit, QWidget
    except Exception:
        QLineEdit = None
        QWidget = object
    chooser = host.findChild(QWidget, object_name)
    if chooser is None:
        return ""
    prop = chooser.property("fileName")
    if isinstance(prop, str) and prop.strip():
        return prop.strip()
    if hasattr(chooser, "fileName") and callable(chooser.fileName):
        val = chooser.fileName()
        if isinstance(val, str) and val.strip():
            return val.strip()
    if QLineEdit is not None:
        line = chooser.findChild(QLineEdit)
        if line is not None:
            return (line.text() or "").strip()
    return ""


def _set_chooser_file_name(host, object_name, path):
    """Set `fileName` on a `Gui::PrefFileChooser` on `host`, if present."""
    try:
        _set_chooser_file_name_live(host, object_name, path)
    except RuntimeError:
        return


def _set_chooser_file_name_live(host, object_name, path):
    """The write, so a deleted chooser is caught by the caller."""
    if host is None:
        return
    try:
        from PySide.QtWidgets import QLineEdit, QWidget
    except Exception:
        QLineEdit = None
        QWidget = object
    chooser = host.findChild(QWidget, object_name)
    if chooser is None:
        return
    chooser.setProperty("fileName", path)
    if hasattr(chooser, "setFileName") and callable(chooser.setFileName):
        chooser.setFileName(path)
    if QLineEdit is not None:
        line = chooser.findChild(QLineEdit)
        if line is not None:
            line.setText(path)


def _cert_would_replace(host=None):
    """True when either TLS field is already filled or `tls-cert.pem` /
    `tls-key.pem` already exists on disk."""
    from freecad.mcp_bridge import config, tls_cert

    if _chooser_file_name(host, "tlsCertFileChooser") or _chooser_file_name(
        host, "tlsKeyFileChooser"
    ):
        return True
    if config.tls_cert_file() or config.tls_key_file():
        return True
    return tls_cert.cert_path().exists() or tls_cert.key_path().exists()


def _confirm_replace_certificate(parent=None):
    """Ask the user before replacing an existing certificate or key."""
    import FreeCADGui as Gui
    from PySide.QtWidgets import QDialog, QMessageBox

    title, body = _cert_replace_dialog_text()
    host = parent if parent is not None else Gui.getMainWindow()
    box = QMessageBox(
        QMessageBox.Question,
        title,
        body,
        QMessageBox.Yes | QMessageBox.No,
        host,
    )
    QDialog.setWindowTitle(box, title)
    box.setObjectName("MCPBridgeConfirmReplaceCertDialog")
    box.setDefaultButton(QMessageBox.No)
    try:
        return box.exec() == QMessageBox.Yes
    finally:
        box.setParent(None)
        box.deleteLater()


def _show_cert_message(title, body, warning, object_name, parent=None):
    """Show a modal information or warning dialog for certificate actions."""
    import FreeCADGui as Gui
    from PySide.QtWidgets import QDialog, QMessageBox

    host = parent if parent is not None else Gui.getMainWindow()
    icon = QMessageBox.Warning if warning else QMessageBox.Information
    box = QMessageBox(icon, title, body, QMessageBox.NoButton, host)
    QDialog.setWindowTitle(box, title)
    box.setObjectName(object_name)
    ok_btn = box.addButton("OK", QMessageBox.AcceptRole)
    box.setDefaultButton(ok_btn)
    box.setEscapeButton(ok_btn)
    try:
        box.exec()
    finally:
        box.setParent(None)
        box.deleteLater()


def _choose_cert_tool_and_key_type(tools, parent=None):
    """Prompt the user to choose a certificate tool from `tools` and a key
    type (`RSA 4096` or `ECDSA P-256`). Return `(tool, key_type)` or `None`."""
    if not tools:
        return None
    try:
        import FreeCADGui as Gui
        from PySide.QtWidgets import (
            QComboBox,
            QDialog,
            QDialogButtonBox,
            QFormLayout,
            QLabel,
            QVBoxLayout,
            QWidget,
        )
    except Exception:
        return tools[0], CERT_KEY_TYPES[0]

    host = (
        parent
        if isinstance(parent, QWidget)
        else Gui.getMainWindow()
    )
    dlg = QDialog(host)
    title = f"{DISPLAY_NAME}: Generate TLS certificate"
    QDialog.setWindowTitle(dlg, title)
    dlg.setObjectName("MCPBridgeCertToolDialog")
    layout = QVBoxLayout(dlg)
    intro = QLabel(
        "Choose the certificate tool and key type for the new TLS "
        "certificate:",
        dlg,
    )
    intro.setObjectName("certToolDialogLabel")
    intro.setWordWrap(True)
    layout.addWidget(intro)

    form = QFormLayout()
    tool_combo = QComboBox(dlg)
    tool_combo.setObjectName("certToolComboBox")
    for tool in tools:
        tool_combo.addItem(_tool_display_label(tool))
    form.addRow("Certificate tool", tool_combo)

    key_combo = QComboBox(dlg)
    key_combo.setObjectName("certKeyTypeComboBox")
    for key_type in CERT_KEY_TYPES:
        key_combo.addItem(key_type)
    form.addRow("Key type", key_combo)
    layout.addLayout(form)

    buttons = QDialogButtonBox(
        QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dlg
    )
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)
    layout.addWidget(buttons)
    try:
        if dlg.exec() != QDialog.Accepted:
            return None
        tool_idx = max(0, min(tool_combo.currentIndex(), len(tools) - 1))
        return tools[tool_idx], key_combo.currentText()
    finally:
        dlg.setParent(None)
        dlg.deleteLater()


def generate_and_show_certificate(parent=None):
    """Generate a self-signed TLS certificate and private key covering
    `127.0.0.1`, `localhost`, every listed address, loopback included,
    and each host name, with a certificate tool on this machine. Fill
    both TLS file fields, and show the path, the covered addresses,
    and the expiry. With no tool found, say where to install one and
    make nothing."""
    from freecad.mcp_bridge import config, tls_cert, token_file

    host = parent
    if host is None:
        try:
            import FreeCADGui as Gui

            host = Gui.getMainWindow()
        except Exception:
            host = None

    try:
        rows = getattr(host, "_listen_rows", None)
        if rows is None:
            rows = config.saved_listen_rows()
        ipv4 = adapter_ipv4(_qt_interface_records(only_up=True))
        entries = bind_entries(rows, ipv4)
        extra_names = [
            row.host for row in rows if row.kind == KIND_NAME and row.host
        ]
        hosts = config.certificate_target_hosts(entries, extra_names)
    except ValueError as exc:
        _show_cert_message(
            f"{DISPLAY_NAME}: TLS certificate",
            str(exc),
            warning=True,
            object_name="MCPBridgeCertInvalidAddressDialog",
            parent=host,
        )
        return None

    cert_tools_mod, tools = _find_cert_tools()
    if not tools or cert_tools_mod is None:
        _show_cert_message(
            f"{DISPLAY_NAME}: TLS certificate",
            _cert_tool_install_hint(),
            warning=True,
            object_name="MCPBridgeCertNoToolDialog",
            parent=host,
        )
        return None

    if _cert_would_replace(host) and not _confirm_replace_certificate(
        parent=host
    ):
        return None

    chosen = _choose_cert_tool_and_key_type(tools, parent=host)
    if chosen is None:
        return None

    wait_cursor = False
    try:
        from PySide.QtCore import Qt
        from PySide.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            app.setOverrideCursor(Qt.WaitCursor)
            wait_cursor = True
    except Exception:
        app = None
    try:
        chosen_tool, chosen_key_type = chosen
        c_path = tls_cert.cert_path()
        k_path = tls_cert.key_path()
        folder = c_path.parent
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        token_file._user_only(folder, is_dir=True)
        cert_tools_mod.generate(
            _tool_id(chosen_tool),
            _key_type_id(chosen_key_type),
            hosts,
            str(c_path),
            str(k_path),
        )
        token_file._user_only(k_path, is_dir=False)
    except Exception as exc:
        if wait_cursor:
            try:
                app.restoreOverrideCursor()
            except Exception:
                pass
            wait_cursor = False
        title, body = _cert_error_dialog_text(exc)
        _show_cert_message(
            title,
            body,
            warning=True,
            object_name="MCPBridgeCertWriteFailureDialog",
            parent=host,
        )
        return None
    finally:
        if wait_cursor:
            try:
                app.restoreOverrideCursor()
            except Exception:
                pass

    c_str, k_str = str(c_path), str(k_path)
    _set_chooser_file_name(host, "tlsCertFileChooser", c_str)
    _set_chooser_file_name(host, "tlsKeyFileChooser", k_str)
    expiry_date = config.certificate_expiry_date(c_str) or ""
    title, body = _cert_generated_dialog_text(
        c_str, hosts, expiry_date=expiry_date
    )
    _show_cert_message(
        title,
        body,
        warning=False,
        object_name="MCPBridgeCertGeneratedDialog",
        parent=host,
    )
    covered = ", ".join(hosts)
    _report_line(
        f"Generated a certificate at {c_str}, covering {covered}."
    )
    return c_str, k_str


def _turn_bridge_on():
    """Start every listed listener. Return `(status, started)`.

    The set-up dialog appears when a token, a certificate, or a
    listen address is missing, and then nothing starts.
    """
    import FreeCAD as App
    import FreeCADGui as Gui

    from freecad.mcp_bridge import config
    from freecad.mcp_bridge.listeners import (
        KIND_ADAPTER,
        KIND_ADDRESS,
        KIND_LEGACY,
        adapter_ipv4,
        adapter_names,
        bind_entries,
        host_already_accepted,
        risk_line,
        start_notes,
    )

    rows = config.saved_listen_rows()
    up = _qt_interface_records(only_up=True)
    every = _qt_interface_records(only_up=False)
    ipv4 = adapter_ipv4(up)
    names = adapter_names(up)
    from freecad.mcp_bridge.pref_pages import _machine

    machine = _machine(every)

    def warn(note: str) -> None:
        App.Console.PrintWarning(f"{LOG_PREFIX} WARNING: {note}\n")

    notes = start_notes(rows, ipv4, machine, names)
    for note in notes:
        warn(note)
    entries = bind_entries(rows, ipv4, machine)
    can_listen = any(
        row.kind in (KIND_ADDRESS, KIND_ADAPTER) for row in rows
    )
    cert_file = config.tls_cert_file()
    key_file = config.tls_key_file()
    non_loopback = [
        entry
        for entry in entries
        if not config.is_loopback_address(entry)
    ]
    if (
        non_loopback or (cert_file.strip() and key_file.strip())
    ) and not config.tls_available():
        # No set-up dialog: no certificate could make TLS work here.
        raise ValueError(config.NO_TLS_MESSAGE)
    need_token = not config.token_hash()
    need_tls = bool(non_loopback) and not (
        cert_file.strip() and key_file.strip()
    )
    # A legacy row is not an empty list. The set-up dialog is only
    # for a list with no rows at all.
    # Host names do not listen. A legacy row is named on its own.
    need_listen = not can_listen and not any(
        row.kind == KIND_LEGACY for row in rows
    )
    if need_token or need_tls or need_listen:
        _show_setup_dialog(need_token, need_tls, need_listen)
        _offline_status()
        return f"{DISPLAY_NAME}: Offline", False, False
    if not entries and not need_listen:
        reason = " ".join(notes) if notes else (
            "The bridge has no listen address it can open."
        )
        _show_status(reason)
        return reason, False, True
    if non_loopback or (cert_file.strip() and key_file.strip()):
        config.build_ssl_context(cert_file, key_file, entries)
    if cert_file.strip() and key_file.strip():
        for line in config.listener_coverage_warnings(cert_file, entries):
            App.Console.PrintWarning(f"{LOG_PREFIX} WARNING: {line}\n")
    pending = _unaccepted(rows, ipv4, machine)
    lines = [line for _key, line in pending]
    first_start = not config.start_warning_accepted()
    if first_start:
        if not _confirm_first_start(lines):
            _offline_status()
            return f"{DISPLAY_NAME}: Offline", False, False
    elif lines and not _confirm_non_loopback_risk(lines):
        _offline_status()
        return f"{DISPLAY_NAME}: Offline", False, False
    bridge.start(
        addresses=entries,
        tls_cert_file=cert_file,
        tls_key_file=key_file,
    )
    if first_start:
        config.accept_start_warning()
    for key, _line in pending:
        config.accept_address(key)
    endpoints = ", ".join(entries)
    warning = (
        "connected agents can run code with your full permissions"
    )
    if non_loopback:
        warning += f"; beyond loopback: {', '.join(non_loopback)}"
    status = (
        f"{DISPLAY_NAME}: Listening on {endpoints} "
        f"(warning: {warning})"
    )
    if notes:
        status = f"{status} {' '.join(notes)}"
    return status, True, bool(notes)


def _offline_status() -> None:
    _show_status(f"{DISPLAY_NAME}: Offline")


def _show_status(text: str) -> None:
    """Put `text` on the status bar, where the user is looking.

    The caller prints the one Report view line.
    """
    import FreeCADGui as Gui

    mw = Gui.getMainWindow()
    if mw:
        mw.statusBar().showMessage(text)
    show_toggle_state()


def _unaccepted(rows, ipv4, machine):
    """`(stored IP, dialog line)` for listeners not yet accepted.

    The stored form is the IP address alone, for both kinds of row.
    One address and port is one line, even when an adapter row and
    a fixed row both name it.
    """
    from freecad.mcp_bridge import config
    from freecad.mcp_bridge.listeners import (
        KIND_ADAPTER,
        KIND_ADDRESS,
        bind_entries,
        format_entry,
        host_already_accepted,
        risk_line,
    )

    stored = config.accepted_listen_addresses()
    bound = set(bind_entries(rows, ipv4, machine))
    pending = []
    seen_entry = set()
    seen_ip = set()
    for row in rows:
        if row.kind == KIND_ADAPTER:
            present = ipv4.get(row.adapter, "")
            if not present:
                continue
            host = present
            entry = format_entry(present, row.port)
        elif row.kind == KIND_ADDRESS:
            host = row.host
            entry = format_entry(row.host, row.port)
        else:
            continue
        if entry not in bound or entry in seen_entry:
            continue
        seen_entry.add(entry)
        if config.is_loopback_address(entry):
            continue
        if host_already_accepted(stored, host) or host in seen_ip:
            continue
        seen_ip.add(host)
        pending.append((host, risk_line(row, host)))
    return pending


class MCPBridgeCommand:
    def GetResources(self):
        import FreeCAD as App

        return {
            "Pixmap": App.MCPBridgeIconPath,
            "MenuText": "MCP Bridge On/Off",
            "ToolTip": (
                "Start or stop the MCP Bridge server in this FreeCAD session"
            ),
        }

    def Activated(self):
        import FreeCAD as App
        import FreeCADGui as Gui

        from freecad.mcp_bridge import config

        started = False
        stopped = False
        reported = False
        bad_listen = False
        try:
            if bridge.is_running():
                bridge.stop()
                status = f"{DISPLAY_NAME}: Offline"
                stopped = True
            else:
                status, started, reported = _turn_bridge_on()
        except Exception as e:
            App.Console.PrintDeveloperError(
                f"{LOG_PREFIX} Error toggling bridge: {e}\n"
            )
            status = f"{DISPLAY_NAME}: Could not start ({e})"
            bad_listen = isinstance(
                e, (OSError, ValueError)
            ) and "Preferences → MCP Bridge" not in str(e)
        mw = Gui.getMainWindow()
        if mw:
            mw.statusBar().showMessage(status)
        show_toggle_state()
        if reported:
            pass
        elif started or stopped:
            App.Console.PrintNotification(status + "\n")
        else:
            warning = status
            if bad_listen:
                warning += (
                    ". Change the listen addresses in Edit → "
                    "Preferences → MCP Bridge."
                )
            App.Console.PrintWarning(warning + "\n")


def _status_button_on_color(rgb):
    """Return the 'On' green hex for a status-bar background (r, g, b)."""
    lin = [
        (c / 255.0) / 12.92
        if c / 255.0 <= 0.04045
        else ((c / 255.0 + 0.055) / 1.055) ** 2.4
        for c in rgb
    ]
    lum = 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    return "#56d364" if lum < 0.2282 else "#116329"


def _refresh_status_button(running, tip):
    """Set the status-bar button's presence, position, text, colour
    and mode icon."""
    import FreeCADGui as Gui
    from PySide.QtGui import QPalette

    from freecad.mcp_bridge import config, file_prompt

    mw = Gui.getMainWindow()
    if mw is None:
        return
    button = _ensure_status_button(mw)
    if button is None:
        return
    button.setText("MCP On" if running else "MCP Off")
    button.setToolTip(tip)
    # The shield or the triangle. Both are one size, so the mode does
    # not change the button's width. The menu's items carry no mark of
    # the mode in force: this icon is what shows it.
    button.setIcon(file_prompt.mode_icon(config.unrestricted()))
    if running:
        bg = mw.statusBar().palette().color(QPalette.Window)
        color = _status_button_on_color((bg.red(), bg.green(), bg.blue()))
        color_rule = f"color: {color}; "
    else:
        color_rule = ""
    button.setStyleSheet(
        f"""
        QToolButton {{ {color_rule}padding-right: 15px; }}
        QToolButton:!hover {{
            background-color: transparent;
            border-color: transparent;
        }}
        """
    )


def _status_bar_widgets(bar):
    """Return (normal, permanent) widget lists in left-to-right bar order.

    Returns (None, None) if the status bar's internal row layout does not
    match Qt's QStatusBar schema.
    """
    from PySide.QtCore import Qt
    from PySide.QtWidgets import QHBoxLayout, QLayout, QWidget

    if bar is None:
        return None, None
    candidates = []
    for layout in bar.findChildren(QHBoxLayout):
        if not isinstance(layout, QLayout):
            continue
        current = layout
        depth = 0
        while isinstance(current, QLayout):
            depth += 1
            current = current.parent()
        if current is bar:
            candidates.append((depth, layout))
    if not candidates:
        return None, None
    max_depth = max(depth for depth, _ in candidates)
    deepest = [
        layout for depth, layout in candidates if depth == max_depth
    ]
    if len(deepest) != 1:
        return None, None
    box = deepest[0]
    slots = {}
    for widget in bar.findChildren(
        QWidget, options=Qt.FindDirectChildrenOnly
    ):
        index = box.indexOf(widget)
        if index >= 0:
            if index in slots:
                return None, None
            slots[index] = widget
    separator = 1
    while separator in slots:
        separator += 1
    count = box.count()
    expected = set(range(1, separator)) | set(
        range(separator + 1, count - 1)
    )
    if separator >= count - 1 or set(slots) != expected:
        return None, None
    return (
        [slots[i] for i in range(1, separator)],
        [slots[i] for i in range(separator + 1, count - 1)],
    )


def _ensure_status_button(mw):
    """Place or remove the permanent status-bar button; return it."""
    import FreeCAD as App
    import FreeCADGui as Gui
    from PySide.QtCore import QEvent, QObject, Qt
    from PySide.QtGui import QIcon
    from PySide.QtWidgets import QMenu, QToolButton

    from freecad.mcp_bridge import config, file_prompt

    class _BarThemeFilter(QObject):
        _reported = False

        def eventFilter(self, _obj, event):
            if event.type() in (
                QEvent.PaletteChange,
                QEvent.StyleChange,
            ):
                try:
                    show_toggle_state()
                except Exception as exc:
                    if not self._reported:
                        self._reported = True
                        App.Console.PrintDeveloperError(
                            f"{LOG_PREFIX} Error refreshing status-bar "
                            f"button: {exc}\n"
                        )
            return False

    def _report_layout_unavailable():
        if not getattr(App, "MCPBridgeLayoutErrorReported", False):
            App.MCPBridgeLayoutErrorReported = True
            App.Console.PrintDeveloperError(
                f"{LOG_PREFIX} Status-bar layout unavailable; "
                "the On/Off button may not be first among the "
                "right-hand items.\n"
            )

    bar = mw.statusBar()
    button = bar.findChild(QToolButton, STATUS_BUTTON_OBJECT_NAME)
    if not config.show_status_button():
        if button is not None:
            bar.removeWidget(button)
            button.setParent(None)
            button.deleteLater()
        return None
    position = config.status_button_position()
    if button is not None:
        if position == STATUS_BUTTON_LEFT:
            _, permanent = _status_bar_widgets(bar)
            if permanent is None:
                _report_layout_unavailable()
            in_place = permanent is None or (
                bool(permanent) and permanent[0] is button
            )
        else:
            in_place = button.property("mcp_position") == position
        if in_place and button.property("mcp_position") == position:
            return button
        bar.removeWidget(button)
        button.setParent(None)
        button.deleteLater()
    button = QToolButton(bar)
    button.setObjectName(STATUS_BUTTON_OBJECT_NAME)
    button.setProperty("mcp_position", position)
    button.setAutoRaise(True)
    button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
    button.setPopupMode(QToolButton.MenuButtonPopup)
    menu = QMenu(button)
    permissions = menu.addMenu(file_prompt.MENU_PERMISSIONS)
    ask_action = permissions.addAction(
        file_prompt.mode_icon(False), file_prompt.MENU_ASK
    )
    ask_action.setCheckable(False)
    ask_action.triggered.connect(lambda *_: _on_mode_menu(False))
    unrestricted_action = permissions.addAction(
        file_prompt.mode_icon(True), file_prompt.MENU_UNRESTRICTED
    )
    unrestricted_action.setCheckable(False)
    unrestricted_action.triggered.connect(lambda *_: _on_mode_menu(True))
    prefs_action = menu.addAction(
        QIcon(icon_path()), file_prompt.MENU_PREFERENCES
    )
    # Some platforms hide a menu's icons unless each item asks for its
    # own. The two mode items are known by theirs.
    for action in (ask_action, unrestricted_action, prefs_action):
        action.setIconVisibleInMenu(True)
    prefs_action.triggered.connect(
        lambda *_: Gui.showPreferences(DISPLAY_NAME, 0)
    )
    button.setMenu(menu)
    # Keep the menus on the button. A caller that only chains
    # menu() can otherwise meet a deleted wrapper.
    button._menu = menu
    button._permissions_menu = permissions
    command = App.MCPBridgeCommand
    button.clicked.connect(lambda *_: Gui.runCommand(command))
    theme_filter = _BarThemeFilter(button)
    button._theme_filter = theme_filter
    bar.installEventFilter(theme_filter)
    if position == STATUS_BUTTON_LEFT:
        normal, _ = _status_bar_widgets(bar)
        if normal is None:
            _report_layout_unavailable()
            bar.addPermanentWidget(button)
        else:
            bar.insertPermanentWidget(len(normal), button)
    else:
        bar.addPermanentWidget(button)
    button.show()
    return button


def show_toggle_state():
    """Show whether the bridge is on, on the toolbar and the status bar,
    and the permission mode on the status-bar button.

    The toolbar button stays pressed while it runs. Both use the same
    tooltip.
    """
    import FreeCAD as App
    import FreeCADGui as Gui

    running = bridge.is_running()
    tip = (
        f"{DISPLAY_NAME}: on (click to stop)"
        if running
        else f"{DISPLAY_NAME}: off (click to start)"
    )
    cmd = Gui.Command.get(App.MCPBridgeCommand)
    for action in cmd.getAction() if cmd is not None else ():
        if not action.isCheckable():
            action.setCheckable(True)
        action.setChecked(running)
        action.setToolTip(tip)
    _refresh_status_button(running, tip)


def inject_ui():
    import FreeCAD as App
    import FreeCADGui as Gui
    from PySide.QtCore import QTimer
    from PySide.QtWidgets import QToolBar

    mw = Gui.getMainWindow()
    if not mw:
        QTimer.singleShot(500, App.MCPBridgeInjectUi)
        return False

    target_tb = None
    for tb in mw.findChildren(QToolBar):
        if (
            tb.windowTitle() == DISPLAY_NAME
            or tb.objectName() == TOOLBAR_OBJECT_NAME
        ):
            target_tb = tb
            break

    _ensure_status_button(mw)
    show_toggle_state()
    mw.update()

    ready = (
        target_tb is not None and target_tb.isVisible() and mw.isVisible()
    )
    if ready and not getattr(App, "MCPBridgeUiReady", False):
        App.MCPBridgeUiReady = True
        App.Console.PrintMessage(f"{LOG_PREFIX} Toolbar ready.\n")
    return ready


class MCPBridgeUiManipulator:
    """Add the MCP Bridge toolbar to each workbench."""

    def modifyToolBars(self):
        import FreeCAD as App
        import FreeCADGui as Gui
        from PySide.QtCore import QTimer

        wb = Gui.activeWorkbench()
        if wb is None or type(wb).__name__ == "NoneWorkbench":
            return []
        if hasattr(App, "MCPBridgeInjectUi"):
            QTimer.singleShot(0, App.MCPBridgeInjectUi)
        # modifyToolBars has no separate operation to create a toolbar:
        # each append entry adds a child to the ToolBarItem named by
        # toolBar. The first entry targets the root item's empty name ("")
        # to create the DISPLAY_NAME toolbar under the root, and the
        # second adds the command to that toolbar.
        return [
            {"append": DISPLAY_NAME, "toolBar": ""},
            {"append": App.MCPBridgeCommand, "toolBar": DISPLAY_NAME},
        ]


class _StatusBarPrefObserver:
    """Refresh the status-bar button and migrate a legacy Port on save."""

    def onChange(self, _grp, param=None):
        from freecad.mcp_bridge import config

        if param in (None, "Port", "ListenAddress"):
            config.migrate_legacy_port()
        if param in (
            None,
            "ShowStatusButton",
            "StatusButtonPosition",
            "PermissionMode",
        ):
            show_toggle_state()
        if param in (None, "PermissionMode"):
            _sync_open_mode_box()


def _apply_mode_choice(want_unrestricted, box):
    """One choice of mode, from Preferences or the On/Off button's menu."""
    from freecad.mcp_bridge import config, file_prompt

    def ask():
        parent = None
        if box is not None:
            try:
                parent = box.window()
            except RuntimeError:
                parent = None
        return file_prompt._ask_unrestricted(parent)

    enabled = file_prompt.commit_mode(box, want_unrestricted, ask)
    config.set_unrestricted(enabled)
    _sync_open_mode_box()
    show_toggle_state()


def _on_mode_chosen(box, index):
    """Choosing Unrestricted in Preferences asks once. Only Yes keeps it.

    Choosing the mode already in force changes nothing and asks nothing.
    """
    from freecad.mcp_bridge import config
    from freecad.mcp_bridge.constants import PERMISSION_MODE_UNRESTRICTED

    want_unrestricted = index == PERMISSION_MODE_UNRESTRICTED
    if want_unrestricted == config.unrestricted():
        return
    _apply_mode_choice(want_unrestricted, box)


def _on_mode_menu(want_unrestricted):
    """A mode chosen in the On/Off button's menu.

    Unrestricted asks first, and No keeps Ask. Ask is at once.
    Choosing the mode already in force does nothing.
    """
    from freecad.mcp_bridge import config

    if want_unrestricted == config.unrestricted():
        return
    _apply_mode_choice(want_unrestricted, None)


def _sync_open_mode_box():
    """Keep an open Preferences box in step with the preference."""
    try:
        from freecad.mcp_bridge import config, file_prompt
        from freecad.mcp_bridge.pref_pages import live_mode_boxes
    except Exception:
        return
    for box in live_mode_boxes():
        file_prompt.set_mode_box_quietly(box, config.unrestricted())


def _show_mode_in_box(box):
    """Show the mode in the Preferences box, and connect it once.

    The box has no preference entry of its own, so FreeCAD's OK writes
    nothing for it: a confirmed choice is the only writer of the mode.
    A box this never reaches shows Ask and changes nothing.
    Each item carries its mode's icon, the same files as the On/Off
    button's menu. The closed box paints the current item's icon.
    """
    from freecad.mcp_bridge import config, file_prompt
    from freecad.mcp_bridge.constants import (
        PERMISSION_MODE_ASK,
        PERMISSION_MODE_UNRESTRICTED,
    )

    box.setItemIcon(PERMISSION_MODE_ASK, file_prompt.mode_icon(False))
    box.setItemIcon(
        PERMISSION_MODE_UNRESTRICTED, file_prompt.mode_icon(True)
    )
    file_prompt.set_mode_box_quietly(box, config.unrestricted())
    if box.property("mcp_mode_hooked"):
        return
    box.setProperty("mcp_mode_hooked", True)
    # activated fires on the user's choice only, never when the code
    # sets the box.
    box.activated.connect(
        lambda index, box=box: _on_mode_chosen(box, index)
    )


def _qt_interface_records(only_up=True):
    """`(interface name, address)` from Qt.

    `only_up` leaves out interfaces that are down. The rows use that.
    Membership uses every interface, up or down. With no Qt network
    module the list is empty and only fixed addresses can start.
    """
    try:
        from PySide.QtNetwork import QHostAddress, QNetworkInterface
    except Exception:
        return []

    records = []
    up = QNetworkInterface.IsUp
    v4 = QHostAddress.IPv4Protocol
    v6 = QHostAddress.IPv6Protocol
    for iface in QNetworkInterface.allInterfaces():
        if only_up and not (iface.flags() & up):
            continue
        name = iface.humanReadableName() or iface.name()
        for entry in iface.addressEntries():
            ip = entry.ip()
            if ip.protocol() not in (v4, v6):
                continue
            records.append((name, ip.toString()))
    return records


def ensure_startup_ui():
    """Wait for the main window, then inject and hook workbench changes."""
    import FreeCAD as App
    import FreeCADGui as Gui
    from PySide.QtCore import QTimer

    from freecad.mcp_bridge import config

    mw = Gui.getMainWindow()
    if mw is None:
        QTimer.singleShot(250, App.MCPBridgeEnsureStartupUi)
        return

    try:
        mw.workbenchActivated
    except AttributeError:
        QTimer.singleShot(250, App.MCPBridgeEnsureStartupUi)
        return

    if not getattr(App, "MCPBridgeHooksConnected", False):
        App.MCPBridgeHooksConnected = True
        mw.workbenchActivated.connect(lambda *_args: App.MCPBridgeInjectUi())
        config.migrate_legacy_port()
        App.MCPBridgePrefGroup = App.ParamGet(PREF_GROUP)
        App.MCPBridgePrefObserver = _StatusBarPrefObserver()
        App.MCPBridgePrefGroup.Attach(App.MCPBridgePrefObserver)

    App.MCPBridgeInjectUi()


def schedule_ui_injection():
    import FreeCAD as App
    from PySide.QtCore import QTimer

    QTimer.singleShot(0, App.MCPBridgeEnsureStartupUi)
    for delay in (1000, 2500, 5000):
        QTimer.singleShot(delay, App.MCPBridgeInjectUi)


App.MCPBridgeInjectUi = inject_ui
App.MCPBridgeEnsureStartupUi = ensure_startup_ui

if App.MCPBridgeCommand in Gui.listCommands():
    try:
        Gui.removeCommand(App.MCPBridgeCommand)
    except Exception:
        pass
MCP_Bridge.register(App.MCPBridgeCommand, MCPBridgeCommand())

if not getattr(App, "MCPBridgePrefAdded", False):
    from freecad.mcp_bridge import config

    try:
        config.migrate_legacy_port()
    except Exception:
        pass
    try:
        from freecad.mcp_bridge.pref_pages import MCPBridgePreferencesPage

        Gui.addPreferencePage(MCPBridgePreferencesPage, DISPLAY_NAME)
        App.MCPBridgePrefAdded = True
    except Exception as exc:
        App.Console.PrintWarning(
            f"{LOG_PREFIX} Could not add preference page: {exc}\n"
        )

if not getattr(App, "MCPBridgeManipulatorAdded", False):
    App.MCPBridgeManipulatorAdded = True
    Gui.addWorkbenchManipulator(MCPBridgeUiManipulator())

schedule_ui_injection()
