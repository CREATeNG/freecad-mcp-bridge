"""The MCP Bridge preference page.

The page is one class. It owns its form, finds every control on
that form, and connects each signal once. A caught error names the
control and is a Report view warning. Nothing here is installed on
the application.
"""

from __future__ import annotations

import os
import traceback

from freecad.mcp_bridge.constants import (
    DEFAULT_PORT,
    DISPLAY_NAME,
    LOG_PREFIX,
)
from freecad.mcp_bridge.listeners import (
    HOST_NAME_LINE,
    KIND_ADAPTER,
    KIND_ADAPTER_LABEL,
    KIND_ADDRESS,
    KIND_FIXED_LABEL,
    KIND_NAME,
    KIND_NAME_LABEL,
    add_refusal,
    already_listed,
    make_row,
    with_row,
    without_index,
)

_MODE_BOXES = []


def ui_path() -> str:
    """Absolute path to the one preferences page."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "Resources", "Ui", "preferences.ui").replace(
        "\\", "/"
    )


def load_form():
    """Load the page's form. FreeCAD builds the preference widgets."""
    import FreeCADGui as Gui

    return Gui.PySideUic.loadUi(ui_path())


def report_wiring(
    control: str, exc: BaseException, with_trace: bool = False
) -> None:
    """One Report view warning, naming `control`.

    A `RuntimeError` is a widget that has gone. A wider catch adds
    the traceback, so a coding fault is not reduced to one line.
    """
    try:
        import FreeCAD as App
    except Exception:
        return
    console = getattr(App, "Console", None)
    if console is None or not hasattr(console, "PrintWarning"):
        return
    detail = f"{exc}"
    if with_trace:
        detail = traceback.format_exc().rstrip()
    try:
        console.PrintWarning(
            f"{LOG_PREFIX} The Preferences page could not wire "
            f"{control} ({detail}).\n"
        )
    except Exception:
        return


def form_child(form, kind, name: str):
    """The child `name` on `form`, or None when it is missing or gone."""
    if form is None:
        return None
    try:
        return form.findChild(kind, name)
    except RuntimeError as exc:
        report_wiring(name, exc)
        return None


def restore_prefs(form) -> None:
    """Call onRestore on each preference widget of `form`."""
    _each_pref(form, "onRestore")


def save_prefs(form) -> None:
    """Call onSave on each preference widget of `form`."""
    _each_pref(form, "onSave")


def _each_pref(form, method: str) -> None:
    from PySide.QtWidgets import QWidget

    try:
        children = form.findChildren(QWidget)
    except RuntimeError as exc:
        report_wiring("form", exc)
        return
    for child in children:
        call = getattr(child, method, None)
        if not callable(call):
            continue
        name = "preference"
        try:
            name = child.objectName() or name
            call()
        except RuntimeError as exc:
            report_wiring(name, exc)
        except Exception as exc:
            report_wiring(name, exc, with_trace=True)


def remember_mode_box(box) -> None:
    """Keep `box` so a mode change elsewhere can refresh it."""
    _MODE_BOXES.append(box)


def live_mode_boxes():
    """Mode boxes whose C++ object is still there."""
    alive = []
    for box in _MODE_BOXES:
        try:
            box.objectName()
        except RuntimeError:
            continue
        except AttributeError:
            alive.append(box)
            continue
        alive.append(box)
    _MODE_BOXES[:] = alive
    return alive


def bind_page(form, page) -> None:
    """Connect the page's buttons once, each in its own guard."""
    from PySide.QtWidgets import QComboBox, QPushButton, QTableWidget

    _hook(
        form_child(form, QPushButton, "addListenButton"),
        "addListenButton",
        lambda button: button.clicked.connect(page.add_listen),
    )
    _hook(
        form_child(form, QPushButton, "removeListenButton"),
        "removeListenButton",
        lambda button: button.clicked.connect(page.remove_listen),
    )
    _hook(
        form_child(form, QPushButton, "generateTokenButton"),
        "generateTokenButton",
        lambda button: button.clicked.connect(page.generate_token),
    )
    _hook(
        form_child(form, QPushButton, "setTokenButton"),
        "setTokenButton",
        lambda button: button.clicked.connect(page.set_token),
    )
    _hook(
        form_child(form, QPushButton, "generateCertButton"),
        "generateCertButton",
        lambda button: button.clicked.connect(page.generate_certificate),
    )
    table = form_child(form, QTableWidget, "listenTable")
    _hook(
        table,
        "listenTable",
        lambda widget: widget.itemSelectionChanged.connect(
            page.show_selected_note
        ),
    )
    box = form_child(form, QComboBox, "permissionModeComboBox")
    if box is not None:
        remember_mode_box(box)


def _hook(widget, name: str, connect) -> None:
    if widget is None:
        return
    try:
        if widget.property("mcp_hooked"):
            return
        connect(widget)
        widget.setProperty("mcp_hooked", True)
    except RuntimeError as exc:
        report_wiring(name, exc)
    except Exception as exc:
        report_wiring(name, exc, with_trace=True)


class PreferencesPage:
    """The one MCP Bridge page. FreeCAD calls save and load."""

    def __init__(self):
        self.form = load_form()
        self.rows = []
        self._prepare_table()
        bind_page(self.form, self)

    def loadSettings(self) -> None:
        """Show the saved list and the saved preference widgets."""
        restore_prefs(self.form)
        from freecad.mcp_bridge import config

        self.rows = config.saved_listen_rows()
        self.form._listen_rows = self.rows
        self._fill()
        self._show_mode()

    def saveSettings(self) -> None:
        """Write the preference widgets and the list. Cancel does not."""
        save_prefs(self.form)
        from freecad.mcp_bridge import config

        config.save_listen_rows(self.rows)

    def add_listen(self) -> None:
        """Ask for one row and add it, unless it is already there."""
        added = ask_add(
            self.form, self._choices(), self._own_name(), self.rows
        )
        if added is None:
            return
        self.rows = with_row(self.rows, added)
        self.form._listen_rows = self.rows
        self._fill()

    def remove_listen(self) -> None:
        """Remove the selected row, the last one included."""
        table = self._table()
        if table is None:
            return
        index = table.currentRow()
        self.rows = without_index(self.rows, index)
        self.form._listen_rows = self.rows
        self._fill()

    def show_selected_note(self) -> None:
        """The note for the selected row, hidden when it has none."""
        self._show_note(self._selected_note())

    def generate_token(self) -> None:
        from freecad.mcp_bridge.init_gui import generate_and_show_token

        generate_and_show_token(parent=self.form)

    def set_token(self) -> None:
        from freecad.mcp_bridge.init_gui import set_pasted_token_from_dialog

        set_pasted_token_from_dialog(parent=self.form)

    def generate_certificate(self) -> None:
        from freecad.mcp_bridge.init_gui import generate_and_show_certificate

        generate_and_show_certificate(parent=self.form)

    def _show_mode(self) -> None:
        from PySide.QtWidgets import QComboBox

        from freecad.mcp_bridge.init_gui import _show_mode_in_box

        box = form_child(self.form, QComboBox, "permissionModeComboBox")
        if box is None:
            return
        try:
            _show_mode_in_box(box)
        except RuntimeError as exc:
            report_wiring("permissionModeComboBox", exc)
        except Exception as exc:
            report_wiring(
                "permissionModeComboBox", exc, with_trace=True
            )

    def _prepare_table(self) -> None:
        table = self._table()
        if table is None:
            return
        try:
            from PySide.QtCore import Qt
            from PySide.QtWidgets import QHeaderView

            table.setColumnCount(3)
            header = table.horizontalHeader()
            header.setVisible(False)
            header.setSectionResizeMode(0, QHeaderView.Stretch)
            header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
            header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
            table.verticalHeader().setVisible(False)
            table.setShowGrid(False)
            table.setHorizontalScrollBarPolicy(
                Qt.ScrollBarAlwaysOff
            )
            table.setWordWrap(False)
        except RuntimeError as exc:
            report_wiring("listenTable", exc)
        except Exception as exc:
            report_wiring("listenTable", exc, with_trace=True)

    def _fill(self) -> None:
        from PySide.QtWidgets import QTableWidgetItem

        table = self._table()
        if table is None:
            return
        ipv4, names, machine = self._facts()
        try:
            table.setRowCount(len(self.rows))
            for index, row in enumerate(self.rows):
                present = ipv4.get(row.adapter, "")
                what, port, kind = _cells(row, present)
                warned = bool(_note(row, machine, names, ipv4))
                values = (what, port, kind)
                for column, text in enumerate(values):
                    item = QTableWidgetItem(text)
                    if warned and column == 0:
                        _set_warning_mark(item)
                    table.setItem(index, column, item)
        except RuntimeError as exc:
            report_wiring("listenTable", exc)
        except Exception as exc:
            report_wiring("listenTable", exc, with_trace=True)
        try:
            table.resizeColumnToContents(1)
            table.resizeColumnToContents(2)
        except RuntimeError as exc:
            report_wiring("listenTable", exc)
        except Exception as exc:
            report_wiring("listenTable", exc, with_trace=True)
        self.show_selected_note()
        self._show_running()

    def _show_running(self) -> None:
        from PySide.QtWidgets import QLabel

        from freecad.mcp_bridge import bridge
        from freecad.mcp_bridge.listeners import (
            bind_entries,
            difference_text,
        )

        label = form_child(
            self.form, QLabel, "listeningDifferenceLabel"
        )
        if label is None:
            return
        listening = None
        try:
            if bridge.is_running():
                listening = bridge.listening_entries()
        except Exception as exc:
            report_wiring("listeningDifferenceLabel", exc)
            listening = None
        ipv4, _names, _machine = self._facts()
        bound = bind_entries(self.rows, ipv4)
        try:
            _set_label(label, difference_text(bound, listening))
        except RuntimeError as exc:
            report_wiring("listeningDifferenceLabel", exc)
        except Exception as exc:
            report_wiring(
                "listeningDifferenceLabel", exc, with_trace=True
            )

    def _selected_note(self) -> str:
        table = self._table()
        if table is None or not self.rows:
            return ""
        try:
            index = table.currentRow()
        except RuntimeError as exc:
            report_wiring("listenTable", exc)
            return ""
        if index < 0 or index >= len(self.rows):
            return ""
        ipv4, names, machine = self._facts()
        return _note(self.rows[index], machine, names, ipv4)

    def _show_note(self, text: str) -> None:
        from PySide.QtWidgets import QLabel

        label = form_child(self.form, QLabel, "listenRowNote")
        if label is None:
            return
        try:
            _set_label(label, text)
        except RuntimeError as exc:
            report_wiring("listenRowNote", exc)

    def _table(self):
        from PySide.QtWidgets import QTableWidget

        return form_child(self.form, QTableWidget, "listenTable")

    def _facts(self):
        from freecad.mcp_bridge.init_gui import _qt_interface_records
        from freecad.mcp_bridge.listeners import (
            adapter_ipv4,
            adapter_names,
        )

        up = _qt_interface_records(only_up=True)
        every = _qt_interface_records(only_up=False)
        return (
            adapter_ipv4(up),
            adapter_names(up),
            _machine(every),
        )

    def _choices(self):
        from freecad.mcp_bridge.init_gui import _qt_interface_records
        from freecad.mcp_bridge.listeners import adapter_choices

        return adapter_choices(_qt_interface_records(only_up=True))

    def _own_name(self) -> str:
        from freecad.mcp_bridge.listeners import own_host_name

        try:
            return own_host_name()
        except Exception as exc:
            report_wiring("host name", exc, with_trace=True)
            return ""


def ask_add(parent, choices, own_name: str, rows):
    """The Add dialog. Return the new row, or None for Cancel.

    A row that is already in `rows` changes nothing.
    """
    from PySide.QtWidgets import (
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QFormLayout,
        QLabel,
        QLineEdit,
        QSpinBox,
        QVBoxLayout,
        QWidget,
    )

    dialog = QDialog(parent)
    dialog.setWindowTitle(f"{DISPLAY_NAME}: Add to Listen addresses")
    dialog.setObjectName("MCPBridgeAddListenDialog")
    kind = QComboBox(dialog)
    kind.addItem(KIND_ADAPTER_LABEL, KIND_ADAPTER)
    kind.addItem(KIND_FIXED_LABEL, KIND_ADDRESS)
    kind.addItem(KIND_NAME_LABEL, KIND_NAME)
    value_label = QLabel("Adapter", dialog)
    holder = QWidget(dialog)
    holder_layout = QVBoxLayout(holder)
    holder_layout.setContentsMargins(0, 0, 0, 0)
    value = QComboBox(holder)
    for label, name in choices:
        value.addItem(label, name)
    address = QLineEdit(holder)
    name_box = QLineEdit(holder)
    name_box.setPlaceholderText(own_name or "")
    for widget in (value, address, name_box):
        holder_layout.addWidget(widget)
    port = QSpinBox(dialog)
    port.setRange(1, 65535)
    port.setValue(DEFAULT_PORT)
    port_label = QLabel("Port", dialog)
    explain = QLabel(HOST_NAME_LINE, dialog)
    explain.setWordWrap(True)
    error = QLabel("", dialog)
    error.setWordWrap(True)
    buttons = QDialogButtonBox(QDialogButtonBox.Cancel, parent=dialog)
    add_button = buttons.addButton("Add", QDialogButtonBox.AcceptRole)
    layout = QVBoxLayout(dialog)
    form = QFormLayout()
    form.addRow("Kind", kind)
    form.addRow(value_label, holder)
    form.addRow(port_label, port)
    layout.addLayout(form)
    layout.addWidget(explain)
    layout.addWidget(error)
    layout.addWidget(buttons)

    def show_kind() -> None:
        selected = kind.currentData()
        value.setVisible(selected == KIND_ADAPTER)
        address.setVisible(selected == KIND_ADDRESS)
        name_box.setVisible(selected == KIND_NAME)
        port.setVisible(selected != KIND_NAME)
        port_label.setVisible(selected != KIND_NAME)
        explain.setVisible(selected == KIND_NAME)
        if selected == KIND_ADAPTER:
            value_label.setText("Adapter")
        elif selected == KIND_ADDRESS:
            value_label.setText("Address")
        else:
            value_label.setText("Name")
        error.setText("")

    def accept() -> None:
        selected = kind.currentData()
        if selected == KIND_ADAPTER:
            entered = value.currentData() or ""
        elif selected == KIND_ADDRESS:
            entered = address.text()
        else:
            entered = name_box.text()
        names = [item for _label, item in choices]
        reason = add_refusal(
            selected, entered, str(port.value()), names
        )
        if reason:
            error.setText(reason)
            return
        row = make_row(selected, entered, str(port.value()))
        if already_listed(rows, row):
            dialog.done(QDialog.Rejected)
            return
        dialog.added = row
        dialog.done(QDialog.Accepted)

    kind.currentIndexChanged.connect(lambda *_args: show_kind())
    add_button.clicked.connect(accept)
    buttons.rejected.connect(dialog.reject)
    show_kind()
    dialog.added = None
    if dialog.exec() != QDialog.Accepted:
        return None
    return dialog.added


def _cells(row, present: str):
    from freecad.mcp_bridge.listeners import cells

    return cells(row, present or None)


def _note(row, machine, names, ipv4) -> str:
    from freecad.mcp_bridge.listeners import row_note

    return row_note(row, machine, names, ipv4)


def _machine(records) -> set[str]:
    import ipaddress

    found = set()
    for _name, address in records:
        text = (address or "").split("%", 1)[0].strip()
        try:
            found.add(str(ipaddress.ip_address(text)))
        except ValueError:
            continue
    return found


def _set_label(label, text: str) -> None:
    label.setText(text)
    label.setVisible(bool(text))


def _set_warning_mark(item) -> None:
    try:
        from PySide.QtWidgets import QApplication, QStyle

        style = QApplication.style()
        if style is None:
            return
        item.setIcon(style.standardIcon(QStyle.SP_MessageBoxWarning))
    except Exception as exc:
        report_wiring("listenTable", exc)
