"""Add or edit a Pi: name, address, login user (hosts.yaml only; nothing is sent to the Pi)."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from raspi.config.loader import ConfigError, check_host
from raspi.core.models import Host


class AddHostDialog(QDialog):
    """On accept, `result_value` is (name, address, user). With `editing`, the name is locked."""

    def __init__(
        self, taken_names: set[str], parent=None, editing: Host | None = None
    ) -> None:
        super().__init__(parent)
        self._taken = taken_names
        self._editing = editing
        self.result_value: tuple[str, str, str] | None = None
        self.setWindowTitle("Edit Pi" if editing else "Add a Pi")
        self.resize(480, 240)

        intro = QLabel(
            "Only ~/.raspi/hosts.yaml changes. SSH key access to the Pi must already "
            "work (see docs/PI-SETUP.md); the table shows right after whether it does."
        )
        intro.setWordWrap(True)
        self.name_edit = QLineEdit(editing.name if editing else "")
        self.name_edit.setAccessibleName("Name")
        self.name_edit.setPlaceholderText("raspi")
        self.name_edit.setReadOnly(editing is not None)
        self.address_edit = QLineEdit(editing.address if editing else "")
        self.address_edit.setAccessibleName("Address")
        self.address_edit.setPlaceholderText("raspi.local or 192.168.1.20")
        self.user_edit = QLineEdit(editing.user if editing else "")
        self.user_edit.setAccessibleName("Login user")
        self.user_edit.setPlaceholderText("pi")
        self.error = QLabel("")
        self.error.setObjectName("banner")
        self.error.setVisible(False)

        form = QFormLayout()
        form.addRow("Name", self.name_edit)
        form.addRow("Address", self.address_edit)
        form.addRow("Login user", self.user_edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            "Save" if editing else "Add"
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addLayout(form)
        layout.addWidget(self.error)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        try:
            name, address, user = check_host(
                self.name_edit.text().strip(),
                self.address_edit.text().strip(),
                self.user_edit.text().strip(),
            )
            if not self._editing and name in self._taken:
                raise ConfigError(f"A Pi named '{name}' already exists")
        except ConfigError as exc:
            self.error.setText(str(exc))
            self.error.setVisible(True)
            return
        self.result_value = (name, address, user)
        self.accept()
