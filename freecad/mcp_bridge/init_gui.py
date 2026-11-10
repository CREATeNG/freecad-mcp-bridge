import os
import sys

import FreeCAD as App
import FreeCADGui as Gui

from freecad.mcp_bridge import MCP_Bridge, bridge
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


LISTEN_ADDRESS_NOTE = (
    "Beyond loopback, the bridge serves HTTPS only, and an MCP client "
    "must trust the certificate in TLS certificate file (PEM) below."
)


def _listen_address_note_text(cert_file=""):
    """Return the note beside Listen addresses, naming `cert_file` when set."""
    cert = (cert_file or "").strip()
    if not cert:
        return LISTEN_ADDRESS_NOTE
    return (
        "Beyond loopback, the bridge serves HTTPS only, and an MCP client "
        "must trust the certificate in TLS certificate file (PEM) below "
        f"({cert})."
    )


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


def _token_dialog_text(token):
    """Return (title, text) for the one-time bearer token dialog."""
    from freecad.mcp_bridge import token_file

    title = f"{DISPLAY_NAME}: Bearer token"
    body = f"""Copy this bearer token now. It will not be shown again.

{token}

It is stored in {token_file.token_path()}, readable only by you. \
The Claude Desktop extension reads it from that file. Other clients \
send it in the Authorization header:
Authorization: Bearer {token}

If you lose it, click Generate new token in Edit → Preferences → \
MCP Bridge. A new token replaces the old one. Every other client \
then needs the new token."""
    return title, body


def _show_generated_token(token, parent=None):
    """Display `token` once in a modal dialog with a Copy Token button."""
    import FreeCADGui as Gui
    from PySide.QtCore import Qt
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
    copy_btn = box.addButton("Copy Token", QMessageBox.ActionRole)
    ok_btn = box.addButton("OK", QMessageBox.AcceptRole)
    box.setDefaultButton(ok_btn)
    box.setEscapeButton(ok_btn)
    try:
        while True:
            box.exec()
            if box.clickedButton() is copy_btn:
                clipboard = QGuiApplication.clipboard()
                if clipboard is not None:
                    clipboard.setText(token)
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


def _token_write_failure_text(exc):
    """Return the dialog text when the token file cannot be written."""
    from freecad.mcp_bridge import token_file

    return (
        "Could not write the bearer token file "
        f"({token_file.token_path()}): {exc}. "
        "Check that the folder is writable, then try again."
    )


def _show_token_write_failure(exc, parent=None):
    """Tell the user that Generate new token could not write the file."""
    import FreeCADGui as Gui
    from PySide.QtWidgets import QDialog, QMessageBox

    title = f"{DISPLAY_NAME}: Bearer token"
    body = _token_write_failure_text(exc)
    host = parent if parent is not None else Gui.getMainWindow()
    box = QMessageBox(
        QMessageBox.Warning,
        title,
        body,
        QMessageBox.NoButton,
        host,
    )
    QDialog.setWindowTitle(box, title)
    box.setObjectName("MCPBridgeTokenWriteFailureDialog")
    ok_btn = box.addButton("OK", QMessageBox.AcceptRole)
    box.setDefaultButton(ok_btn)
    box.setEscapeButton(ok_btn)
    try:
        box.exec()
    finally:
        box.setParent(None)
        box.deleteLater()


def generate_and_show_token(parent=None):
    """Generate a new bearer token, write the token file, and show it once.

    A failed write shows the reason and returns None.
    """
    from freecad.mcp_bridge import config

    try:
        token = config.generate_and_store_token()
    except OSError as exc:
        _show_token_write_failure(exc, parent=parent)
        return None
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
    """Return a platform-specific note explaining where to install a
    certificate tool when none was found on this machine."""
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
        "No certificate tool was found on this machine, so this "
        "certificate was generated with the built-in RSA 2048 "
        f"fallback. To generate RSA 4096 or ECDSA P-256 certificates: "
        f"{where}"
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


def _cert_generated_dialog_text(
    cert_file, hosts, expiry_date="", install_hint=""
):
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
    if install_hint:
        body += f"{install_hint}\n\n"
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
        try:
            chooser.setFileName(path)
        except Exception:
            pass
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
    `127.0.0.1`, `localhost`, and any addresses beyond loopback in `Listen
    addresses`, fill both TLS file fields, and show the certificate path,
    covered addresses, and expiry."""
    from freecad.mcp_bridge import config, tls_cert, token_file

    host = parent
    if host is None:
        try:
            import FreeCADGui as Gui

            host = Gui.getMainWindow()
        except Exception:
            host = None

    raw_entries = None
    if host is not None:
        try:
            from PySide.QtWidgets import QTextEdit

            edit = host.findChild(QTextEdit, "listenAddressTextEdit")
            if edit is not None:
                raw_entries = edit.toPlainText()
        except Exception:
            raw_entries = None

    try:
        entries = (
            config.parse_listen_addresses(raw_entries)
            if raw_entries is not None
            else config.listen_addresses()
        )
        hosts = config.certificate_target_hosts(entries)
    except ValueError as exc:
        _show_cert_message(
            f"{DISPLAY_NAME}: TLS certificate",
            str(exc),
            warning=True,
            object_name="MCPBridgeCertInvalidAddressDialog",
            parent=host,
        )
        return None

    if _cert_would_replace(host) and not _confirm_replace_certificate(
        parent=host
    ):
        return None

    cert_tools_mod, tools = _find_cert_tools()
    install_hint = ""
    chosen = None
    if tools and cert_tools_mod is not None:
        chosen = _choose_cert_tool_and_key_type(tools, parent=host)
        if chosen is None:
            return None
    else:
        install_hint = _cert_tool_install_hint()

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
        if chosen is not None and cert_tools_mod is not None:
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
        else:
            c_path, k_path = tls_cert.generate_and_store_certificate(hosts)
            token_file._user_only(k_path, is_dir=False)
    except Exception as exc:
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
    if host is not None:
        try:
            from PySide.QtWidgets import QLabel
        except Exception:
            QLabel = object
        try:
            note = host.findChild(QLabel, "listenAddressNote")
            if note is not None:
                note.setText(_listen_address_note_text(c_str))
        except Exception:
            pass
    expiry_date = config.certificate_expiry_date(c_str) or ""
    title, body = _cert_generated_dialog_text(
        c_str, hosts, expiry_date=expiry_date, install_hint=install_hint
    )
    _show_cert_message(
        title,
        body,
        warning=False,
        object_name="MCPBridgeCertGeneratedDialog",
        parent=host,
    )
    return c_str, k_str


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
        bad_listen = False
        try:
            if bridge.is_running():
                bridge.stop()
                status = f"{DISPLAY_NAME}: Offline"
                stopped = True
            else:
                entries = config.listen_addresses()
                cert_file = config.tls_cert_file()
                key_file = config.tls_key_file()
                for entry in entries:
                    config.parse_listen_entry(entry)
                non_loopback = [
                    entry
                    for entry in entries
                    if not config.is_loopback_address(entry)
                ]
                if non_loopback or (
                    cert_file.strip() and key_file.strip()
                ):
                    config.build_ssl_context(
                        cert_file, key_file, non_loopback
                    )
                unaccepted = [
                    entry
                    for entry in non_loopback
                    if not config.is_address_accepted(entry)
                ]
                first_start = not config.start_warning_accepted()
                if first_start:
                    if not _confirm_first_start(unaccepted):
                        status = f"{DISPLAY_NAME}: Offline"
                        mw = Gui.getMainWindow()
                        if mw:
                            mw.statusBar().showMessage(status)
                        show_toggle_state()
                        App.Console.PrintNotification(status + "\n")
                        return
                elif unaccepted and not _confirm_non_loopback_risk(
                    unaccepted
                ):
                    status = f"{DISPLAY_NAME}: Offline"
                    mw = Gui.getMainWindow()
                    if mw:
                        mw.statusBar().showMessage(status)
                    show_toggle_state()
                    App.Console.PrintNotification(status + "\n")
                    return
                from freecad.mcp_bridge import token_file

                if not token_file.read_token():
                    if generate_and_show_token() is None:
                        status = f"{DISPLAY_NAME}: Offline"
                        mw = Gui.getMainWindow()
                        if mw:
                            mw.statusBar().showMessage(status)
                        show_toggle_state()
                        App.Console.PrintWarning(
                            f"{LOG_PREFIX} Could not write the bearer "
                            "token file. Check that the folder is "
                            "writable, then try again.\n"
                        )
                        return
                bridge.start(
                    addresses=entries,
                    tls_cert_file=cert_file,
                    tls_key_file=key_file,
                )
                if first_start:
                    config.accept_start_warning()
                for entry in unaccepted:
                    config.accept_address(entry)
                endpoints = ", ".join(entries)
                warning = (
                    "connected agents can run code with your full "
                    "permissions"
                )
                if non_loopback:
                    warning += f"; beyond loopback: {', '.join(non_loopback)}"
                status = (
                    f"{DISPLAY_NAME}: Listening on {endpoints} "
                    f"(warning: {warning})"
                )
                started = True
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
        if started or stopped:
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
    if not file_prompt.status_button_shown(
        config.show_status_button(), config.unrestricted()
    ):
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
    ask_action = menu.addAction(
        file_prompt.mode_icon(False), file_prompt.MENU_ASK
    )
    ask_action.triggered.connect(lambda *_: _on_mode_menu(False))
    unrestricted_action = menu.addAction(
        file_prompt.mode_icon(True), file_prompt.MENU_UNRESTRICTED
    )
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
        if param in (None, "TlsCertFile"):
            _sync_open_listen_note()


def _apply_mode_choice(want_unrestricted, box):
    """One choice of mode, from Preferences or the On/Off button's menu."""
    from freecad.mcp_bridge import config, file_prompt

    def ask():
        parent = box.window() if box is not None else None
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
        from PySide.QtWidgets import QComboBox
        import FreeCADGui as Gui
    except Exception:
        return
    from freecad.mcp_bridge import config, file_prompt

    mw = Gui.getMainWindow()
    if mw is None:
        return
    box = mw.findChild(QComboBox, "permissionModeComboBox")
    file_prompt.set_mode_box_quietly(box, config.unrestricted())


def _show_mode_in_box(box):
    """Show the mode in the Preferences box, and connect it once.

    The box has no preference entry of its own, so FreeCAD's OK writes
    nothing for it: a confirmed choice is the only writer of the mode.
    A box this never reaches shows Ask and changes nothing.
    """
    from freecad.mcp_bridge import config, file_prompt

    file_prompt.set_mode_box_quietly(box, config.unrestricted())
    if box.property("mcp_mode_hooked"):
        return
    box.setProperty("mcp_mode_hooked", True)
    # activated fires on the user's choice only, never when the code
    # sets the box.
    box.activated.connect(
        lambda index, box=box: _on_mode_chosen(box, index)
    )


def _sync_open_listen_note():
    """Keep the Listen addresses TLS note in step with TlsCertFile."""
    try:
        from PySide.QtWidgets import QLabel
        import FreeCADGui as Gui
    except Exception:
        return
    from freecad.mcp_bridge import config

    mw = Gui.getMainWindow()
    if mw is None:
        return
    label = mw.findChild(QLabel, "listenAddressNote")
    if label is not None:
        label.setText(_listen_address_note_text(config.tls_cert_file()))


def _install_pref_page_filter(mw):
    """Connect `generateTokenButton` and the Permission mode box when
    the Preferences page opens."""
    try:
        from PySide.QtCore import QEvent, QObject
        from PySide.QtWidgets import (
            QApplication,
            QComboBox,
            QLabel,
            QPushButton,
        )
    except Exception:
        return

    app = QApplication.instance()
    if app is None or getattr(mw, "_mcp_pref_filter", None) is not None:
        return

    class _PrefPageFilter(QObject):
        def eventFilter(self, obj, event):
            if (
                event.type() == QEvent.Show
                and isinstance(obj, QPushButton)
                and obj.objectName() == "generateTokenButton"
                and not obj.property("mcp_token_hooked")
            ):
                obj.setProperty("mcp_token_hooked", True)
                obj.clicked.connect(
                    lambda *_: generate_and_show_token(parent=obj.window())
                )
            if (
                event.type() == QEvent.Show
                and isinstance(obj, QPushButton)
                and obj.objectName() == "generateCertButton"
                and not obj.property("mcp_cert_hooked")
            ):
                obj.setProperty("mcp_cert_hooked", True)
                obj.clicked.connect(
                    lambda *_: generate_and_show_certificate(
                        parent=obj.window()
                    )
                )
            if (
                event.type() == QEvent.Show
                and isinstance(obj, QComboBox)
                and obj.objectName() == "permissionModeComboBox"
            ):
                _show_mode_in_box(obj)
            if (
                event.type() == QEvent.Show
                and isinstance(obj, QLabel)
                and obj.objectName() == "listenAddressNote"
            ):
                from freecad.mcp_bridge import config

                obj.setText(
                    _listen_address_note_text(config.tls_cert_file())
                )
            return False

    flt = _PrefPageFilter(mw)
    mw._mcp_pref_filter = flt
    app.installEventFilter(flt)


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
        _install_pref_page_filter(mw)

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
    _prefs_ui = preferences_ui_path()
    if os.path.isfile(_prefs_ui):
        try:
            Gui.addPreferencePage(_prefs_ui, DISPLAY_NAME)
            App.MCPBridgePrefAdded = True
        except Exception as exc:
            App.Console.PrintWarning(
                f"{LOG_PREFIX} Could not add preference page: {exc}\n"
            )

if not getattr(App, "MCPBridgeManipulatorAdded", False):
    App.MCPBridgeManipulatorAdded = True
    Gui.addWorkbenchManipulator(MCPBridgeUiManipulator())

schedule_ui_injection()
