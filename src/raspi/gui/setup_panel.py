"""First-run panel: how to register a Pi, with a link to the full guide."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from raspi import APP_NAME

# Source checkouts only: docs are not shipped inside an installed package.
GUIDE = Path(__file__).resolve().parents[3] / "docs" / "PI-SETUP.md"

HELP_TEXT = (
    "No Raspberry Pi registered yet.\n"
    "1. In a terminal, trust the Pi's SSH host key once:   ssh <user>@<address>\n"
    "    (compare the fingerprint with: ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub)\n"
    "2. Put your SSH key on the Pi:   .\\scripts\\setup-pi-ssh.ps1 -PiHost <address> -User <user>\n"
    "3. Click “Add a Pi…” (Ctrl+N), or run:   raspi host add NAME ADDRESS USER"
)


def open_guide(parent: object | None = None) -> bool:
    """Open docs/PI-SETUP.md in the default editor; say so when it is not there."""
    if GUIDE.is_file() and QDesktopServices.openUrl(QUrl.fromLocalFile(str(GUIDE))):
        return True
    QMessageBox.information(
        parent, APP_NAME, f"The setup guide was not found at:\n{GUIDE}"
    )
    return False


class SetupPanel(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("setup")
        self.setAccessibleName("How to add a Raspberry Pi")
        text = QLabel(HELP_TEXT)
        text.setObjectName("setupText")
        text.setWordWrap(True)
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.guide_button = QPushButton("Open setup guide")
        self.guide_button.clicked.connect(lambda: open_guide(self))
        row = QHBoxLayout()
        row.addWidget(self.guide_button)
        row.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addWidget(text)
        layout.addLayout(row)
