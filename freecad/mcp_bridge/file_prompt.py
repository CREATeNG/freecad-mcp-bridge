"""Ask before an agent's code runs.

The bridge already has the text. The dialog shows that text, and the
job runs that same text. A file also shows its path. The ask happens
on FreeCAD's main thread. The call's response timeout includes it.
"""

from freecad.mcp_bridge.constants import DISPLAY_NAME

RUN = "run"
DECLINE = "decline"
FILE_DIALOG = "MCPBridgeRunFileDialog"
CODE_DIALOG = "MCPBridgeRunCodeDialog"
MODE_DIALOG = "MCPBridgeUnrestrictedDialog"
UNRESTRICTED_BUTTON = "Unrestricted!"
# The warning colour of the Unrestricted! button's text.
WARNING_COLOR = "#c96a00"

FILE_INTRO = (
    "An agent asks to run this file in FreeCAD with your full "
    "permissions:"
)
CODE_INTRO = (
    "An agent asks to run this code in FreeCAD with your full "
    "permissions:"
)
CODE_DECLINE = "The user declined to run this code."
# The On/Off button's menu: Permissions, with Ask and Unrestricted
# in a submenu, then the item that opens the Preferences page.
MENU_PERMISSIONS = "Permissions"
MENU_ASK = "Ask"
MENU_UNRESTRICTED = "Unrestricted"
MENU_PREFERENCES = "MCP Bridge Preferences"
MODE_TOOLTIP = (
    "Ask shows you an agent's code first. Unrestricted runs it unseen."
)
MODE_BODY = (
    "In Unrestricted mode, any code an agent sends runs with your "
    "full permissions, unseen. Are you really sure?"
)


def status_button_shown(show_preference: bool, unrestricted: bool) -> bool:
    """Whether the On/Off button is on the status bar.

    The button carries the mode's icon, so Unrestricted shows it
    whatever the Show the On/Off button preference says.
    """
    return bool(show_preference) or bool(unrestricted)


def mode_icon(unrestricted: bool):
    """The mode's icon: the shield with a tick, or the warning triangle."""
    from PySide.QtGui import QIcon

    from freecad.mcp_bridge.resources import mode_icon_path

    return QIcon(mode_icon_path(unrestricted))


def set_mode_box_quietly(box, unrestricted: bool) -> None:
    """Show a mode in the Preferences box without its handler running."""
    if box is None:
        return
    from freecad.mcp_bridge.constants import (
        PERMISSION_MODE_ASK,
        PERMISSION_MODE_UNRESTRICTED,
    )

    index = (
        PERMISSION_MODE_UNRESTRICTED if unrestricted else PERMISSION_MODE_ASK
    )
    box.blockSignals(True)
    try:
        box.setCurrentIndex(index)
    finally:
        box.blockSignals(False)


def commit_mode(box, want_unrestricted: bool, ask) -> bool:
    """One user choice of mode. Return whether Unrestricted is now on.

    `ask` runs once, and only to go Unrestricted. Going back to Ask
    does not ask. `box` is the Preferences box, or None.
    """
    if not want_unrestricted:
        set_mode_box_quietly(box, False)
        return False
    enabled = ask() == "Yes"
    set_mode_box_quietly(box, enabled)
    return enabled


def decline_message(path) -> str:
    """What the agent is told when the user chooses Don't run."""
    if not path:
        return CODE_DECLINE
    return f"The user declined to run '{path}'."


def decide(ask, *, unrestricted: bool, go_unrestricted) -> str:
    """Return ``run`` or ``decline``.

    `ask` is called with ``"run"`` or ``"mode"`` and returns the
    button the user pressed. A no on the second dialog returns to the
    first. Only Yes switches to Unrestricted, and this code then runs.
    """
    if unrestricted:
        return RUN
    while True:
        choice = ask("run")
        if choice == "Run":
            return RUN
        if choice != UNRESTRICTED_BUTTON:
            return DECLINE
        if ask("mode") == "Yes":
            go_unrestricted()
            return RUN


def confirm(path, code: str) -> str:
    """Show the dialogs on the main thread. Return ``run`` or ``decline``."""
    from freecad.mcp_bridge import config

    def ask(kind):
        if kind == "mode":
            return _ask_unrestricted()
        return _ask_run(path, code)

    def go_unrestricted():
        config.set_unrestricted(True)
        _refresh_button()

    return decide(
        ask,
        unrestricted=config.unrestricted(),
        go_unrestricted=go_unrestricted,
    )


def _refresh_button():
    """Show the new mode on the On/Off button."""
    try:
        from freecad.mcp_bridge.init_gui import show_toggle_state
    except Exception:
        return
    show_toggle_state()


def _ask_run(path, code: str) -> str:
    import FreeCADGui as Gui
    from PySide.QtWidgets import (
        QDialog,
        QHBoxLayout,
        QLabel,
        QPlainTextEdit,
        QPushButton,
        QVBoxLayout,
    )

    dialog = QDialog(Gui.getMainWindow())
    if path:
        dialog.setObjectName(FILE_DIALOG)
        dialog.setWindowTitle(f"{DISPLAY_NAME}: Run this file?")
        intro = FILE_INTRO
    else:
        dialog.setObjectName(CODE_DIALOG)
        dialog.setWindowTitle(f"{DISPLAY_NAME}: Run this code?")
        intro = CODE_INTRO
    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel(intro))
    if path:
        layout.addWidget(QLabel(path))
    text = QPlainTextEdit()
    text.setReadOnly(True)
    text.setPlainText(code)
    text.setMinimumSize(520, 240)
    layout.addWidget(text)
    run = QPushButton("Run")
    decline = QPushButton("Don't run")
    unrestricted = QPushButton(UNRESTRICTED_BUTTON)
    unrestricted.setIcon(mode_icon(True))
    # A text colour alone leaves the platform's own button frame.
    unrestricted.setStyleSheet(
        f"QPushButton {{ color: {WARNING_COLOR}; font-weight: bold; }}"
    )
    row = QHBoxLayout()
    row.addWidget(unrestricted)
    row.addStretch(1)
    row.addWidget(run)
    row.addWidget(decline)
    layout.addLayout(row)
    choice = {"value": "Don't run"}

    def finish(value):
        choice["value"] = value
        dialog.accept()

    run.clicked.connect(lambda: finish("Run"))
    unrestricted.clicked.connect(
        lambda: finish(UNRESTRICTED_BUTTON)
    )
    decline.clicked.connect(lambda: finish("Don't run"))
    decline.setDefault(True)
    decline.setAutoDefault(True)
    dialog.rejected.connect(lambda: finish("Don't run"))
    dialog.exec()
    dialog.deleteLater()
    return choice["value"]


def _ask_unrestricted(parent=None) -> str:
    """Ask the second question. `parent` is the Preferences window."""
    import FreeCADGui as Gui
    from PySide.QtWidgets import QDialog, QMessageBox

    if parent is None:
        parent = Gui.getMainWindow()
    title = f"{DISPLAY_NAME}: Unrestricted?"
    box = QMessageBox(
        QMessageBox.Warning,
        title,
        MODE_BODY,
        QMessageBox.NoButton,
        parent,
    )
    QDialog.setWindowTitle(box, title)
    box.setObjectName(MODE_DIALOG)
    yes = box.addButton("Yes", QMessageBox.AcceptRole)
    no = box.addButton("No", QMessageBox.RejectRole)
    box.setDefaultButton(no)
    box.setEscapeButton(no)
    box.exec()
    chosen = "Yes" if box.clickedButton() is yes else "No"
    box.deleteLater()
    return chosen
