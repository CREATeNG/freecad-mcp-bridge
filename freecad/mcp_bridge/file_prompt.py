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
TRUST_DIALOG = "MCPBridgeTrustAnyCodeDialog"
TRUST_BUTTON = "Trust any code"

FILE_INTRO = (
    "An agent asks to run this file in FreeCAD with your full "
    "permissions:"
)
CODE_INTRO = (
    "An agent asks to run this code in FreeCAD with your full "
    "permissions:"
)
CODE_DECLINE = "The user declined to run this code."
ASKS_BEFORE = "MCP: asks before code"
TRUSTS_ANY = "MCP: trusts any code"
TRUST_TOOLTIP = (
    "Any code an agent sends runs unseen, with your full permissions."
)
TRUST_BODY = (
    "Any code an agent sends will then run with your full "
    "permissions, unseen. Are you really sure?"
)


def trust_checkbox_sticks(checked: bool, answer: str) -> bool:
    """Whether Trust any code stays ticked.

    Turning it off needs no dialog. Turning it on sticks only on Yes.
    """
    if not checked:
        return False
    return answer == "Yes"


def trust_after_gesture(turning_on: bool, answer: str) -> bool:
    """The preference after one click. Turning off does not ask."""
    if not turning_on:
        return False
    return answer == "Yes"


def marker_text(trust_on: bool) -> str:
    """Status-bar wording. The marker is shown in either state."""
    return TRUSTS_ANY if trust_on else ASKS_BEFORE


def marker_warns(trust_on: bool) -> bool:
    """The warning triangle is shown only while trust is on."""
    return bool(trust_on)


def trust_label_visible(trust_on: bool) -> bool:
    """The marker stays up whether or not trust is on."""
    del trust_on
    return True


def set_checkbox_quietly(box, checked: bool) -> None:
    """Set a checkbox without its toggled handler running again."""
    if box is None:
        return
    box.blockSignals(True)
    try:
        box.setChecked(checked)
    finally:
        box.blockSignals(False)


def commit_checkbox(box, want_on: bool, ask) -> bool:
    """One user tick. `ask` runs once, and only to turn trust on."""
    if not want_on:
        set_checkbox_quietly(box, False)
        return False
    enabled = ask() == "Yes"
    set_checkbox_quietly(box, enabled)
    return enabled


def decline_message(path) -> str:
    """What the agent is told when the user chooses Don't run."""
    if not path:
        return CODE_DECLINE
    return f"The user declined to run '{path}'."


def decide(ask, *, trusting: bool, set_trust) -> str:
    """Return ``run`` or ``decline``.

    `ask` is called with ``"file"`` or ``"trust"`` and returns the
    button the user pressed. A no on the second dialog returns to the
    first. Only Yes turns trust on, and this file then runs.
    """
    if trusting:
        return RUN
    while True:
        choice = ask("file")
        if choice == "Run":
            return RUN
        if choice != TRUST_BUTTON:
            return DECLINE
        if ask("trust") == "Yes":
            set_trust(True)
            return RUN


def confirm(path: str, code: str) -> str:
    """Show the dialogs on the main thread. Return ``run`` or ``decline``."""
    from freecad.mcp_bridge import config

    def ask(kind):
        if kind == "trust":
            return _ask_trust()
        return _ask_file(path, code)

    def set_trust(enabled):
        config.set_trust_any_code(enabled)
        _refresh_marker()

    return decide(
        ask, trusting=config.trust_any_code(), set_trust=set_trust
    )


def _refresh_marker():
    try:
        from freecad.mcp_bridge.init_gui import refresh_trust_marker
    except Exception:
        return
    refresh_trust_marker()


def _ask_file(path: str, code: str) -> str:
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
        dialog.setWindowTitle(f"{DISPLAY_NAME} — Run this file?")
        intro = FILE_INTRO
    else:
        dialog.setObjectName(CODE_DIALOG)
        dialog.setWindowTitle(f"{DISPLAY_NAME} — Run this code?")
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
    trust = QPushButton(TRUST_BUTTON)
    row = QHBoxLayout()
    row.addWidget(trust)
    row.addStretch(1)
    row.addWidget(run)
    row.addWidget(decline)
    layout.addLayout(row)
    choice = {"value": "Don't run"}

    def finish(value):
        choice["value"] = value
        dialog.accept()

    run.clicked.connect(lambda: finish("Run"))
    trust.clicked.connect(lambda: finish(TRUST_BUTTON))
    decline.clicked.connect(lambda: finish("Don't run"))
    decline.setDefault(True)
    decline.setAutoDefault(True)
    dialog.rejected.connect(lambda: finish("Don't run"))
    dialog.exec()
    dialog.deleteLater()
    return choice["value"]


def _ask_trust(parent=None) -> str:
    """Ask the second question. `parent` is the Preferences window."""
    import FreeCADGui as Gui
    from PySide.QtWidgets import QDialog, QMessageBox

    if parent is None:
        parent = Gui.getMainWindow()
    title = f"{DISPLAY_NAME} — Trust any code?"
    body = TRUST_BODY
    box = QMessageBox(
        QMessageBox.Warning,
        title,
        body,
        QMessageBox.NoButton,
        parent,
    )
    QDialog.setWindowTitle(box, title)
    box.setObjectName(TRUST_DIALOG)
    yes = box.addButton("Yes", QMessageBox.AcceptRole)
    no = box.addButton("No", QMessageBox.RejectRole)
    box.setDefaultButton(no)
    box.setEscapeButton(no)
    box.exec()
    chosen = "Yes" if box.clickedButton() is yes else "No"
    box.deleteLater()
    return chosen
