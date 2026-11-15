"""Dialog to add a route: Pi, optional project, hostname and local service."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from raspi.core.models import Host
from raspi.services import routing


class AddRouteDialog(QDialog):
    """Asks for the Pi, an optional project, the public hostname and the local port.

    `result_value` = (host, hostname, service, project); project "" means the Pi's shared tunnel."""

    def __init__(
        self,
        hosts: list[Host],
        zone_name: str,
        parent=None,
        prefill: tuple[str, str, str, str] | None = None,
    ) -> None:
        """`prefill` = (host, hostname, service, project) turns this into an edit: only the service can change."""
        super().__init__(parent)
        self.result_value: tuple[str, str, str, str] | None = None
        self.setWindowTitle("Edit a route" if prefill else "Add a route")
        self.host_box = QComboBox()
        self.host_box.addItems([h.name for h in hosts])
        self.hostname = QLineEdit()
        self.hostname.setPlaceholderText(f"app.{zone_name or 'example.com'}")
        self.service = QLineEdit("http://localhost:")
        self.project = QLineEdit()
        self.project.setPlaceholderText(
            "optional, e.g. web-game (empty = one tunnel shared by the Pi)"
        )
        self.project.setAccessibleName("Project")
        self.tunnel_label = QLabel("")
        self.tunnel_label.setAccessibleName("Tunnel name")
        if prefill:
            host, hostname, service, project = prefill
            self.host_box.setCurrentText(host)
            self.hostname.setText(hostname)
            self.service.setText(service)
            self.project.setText(project)
            self.host_box.setEnabled(False)
            self.hostname.setReadOnly(True)
            self.project.setReadOnly(True)
        self.host_box.currentTextChanged.connect(self._update_tunnel_label)
        self.project.textChanged.connect(self._update_tunnel_label)
        self._update_tunnel_label()
        form = QFormLayout()
        form.addRow("Pi running the app", self.host_box)
        form.addRow("Project", self.project)
        form.addRow("Tunnel", self.tunnel_label)
        form.addRow("Public hostname", self.hostname)
        form.addRow("Local service", self.service)
        self.error = QLabel("")
        self.error.setObjectName("banner")
        self.error.setVisible(False)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Preview")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel("Nothing changes until you confirm the preview on the next screen.")
        )
        layout.addLayout(form)
        layout.addWidget(self.error)
        layout.addWidget(buttons)

    def _update_tunnel_label(self, *_: object) -> None:
        host, project = self.host_box.currentText(), self.project.text().strip()
        self.tunnel_label.setText(
            routing.tunnel_name_for(host, project or None) if host else ""
        )

    def _accept(self) -> None:
        if not self.host_box.currentText() or not self.hostname.text().strip():
            self.error.setText("Choose a Pi and type the public hostname.")
            self.error.setVisible(True)
            return
        self.result_value = (
            self.host_box.currentText(),
            self.hostname.text(),
            self.service.text(),
            self.project.text().strip(),
        )
        self.accept()
