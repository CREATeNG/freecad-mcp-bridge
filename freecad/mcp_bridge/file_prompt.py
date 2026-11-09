"""Ask before execute_python_file runs a file.

The bridge has already read the file. The dialog shows that text, and
the job runs that same text. The ask happens on FreeCAD's main thread,
before the response timeout starts.
"""

from freecad.mcp_bridge.constants import DISPLAY_NAME

RUN = "run"
DECLINE = "decline"
FILE_DIALOG = "MCPBridgeRunFileDialog"
TRUST_DIALOG = "MCPBridgeTrustAnyFileDialog"


def decline_message(path: str) -> str:
    """What the agent is told when the user chooses Don't run."""
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
        if choice != "Trust any file":
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
        config.set_trust_any_file(enabled)
        _refresh_marker()

    return decide(
        ask, trusting=config.trust_any_file(), set_trust=set_trust
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
    dialog.setObjectName(FILE_DIALOG)
    dialog.setWindowTitle(f"{DISPLAY_NAME} — Run this file?")
    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel(path))
    text = QPlainTextEdit()
    text.setReadOnly(True)
    text.setPlainText(code)
    text.setMinimumSize(520, 240)
    layout.addWidget(text)
    run = QPushButton("Run")
    decline = QPushButton("Don't run")
    trust = QPushButton("Trust any file")
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
    trust.clicked.connect(lambda: finish("Trust any file"))
    decline.clicked.connect(lambda: finish("Don't run"))
    decline.setDefault(True)
    decline.setAutoDefault(True)
    dialog.rejected.connect(lambda: finish("Don't run"))
    dialog.exec()
    dialog.deleteLater()
    return choice["value"]


def _ask_trust() -> str:
    import FreeCADGui as Gui
    from PySide.QtWidgets import QDialog, QMessageBox

    title = f"{DISPLAY_NAME} — Trust any file?"
    body = (
        "Every file an agent sends will then run with your full "
        "permissions, unseen. Are you really sure?"
    )
    box = QMessageBox(
        QMessageBox.Warning,
        title,
        body,
        QMessageBox.NoButton,
        Gui.getMainWindow(),
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
