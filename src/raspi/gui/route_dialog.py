"""Ask for the public hostname and the local port of an app (everything else is derived)."""

from __future__ import annotations

from PySide6.QtCore import Slot
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from raspi.core.models import AppRef, Host
from raspi.core.routes import RouteError, check_hostname
from raspi.gui.workers import submit
from raspi.services.app_route import check_port, detect_ports
from raspi.services.routing import tunnel_name_for
from raspi.ssh.transport import Transport


class RouteSetupDialog(QDialog):
    """On accept, `result_value` is (hostname, port)."""

    def __init__(
        self,
        host: Host,
        app: AppRef,
        zone_name: str,
        transport: Transport,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.result_value: tuple[str, int] | None = None
        self.setWindowTitle(f"Set up a route - {app.name}")
        self.resize(560, 280)

        intro = QLabel(
            f"Put <b>{app.name}</b> (on {host.name}) on the internet through Cloudflare. "
            f"It gets its own tunnel, <code>{tunnel_name_for(host.name, app.name)}</code>. "
            "You will see every change before anything happens."
        )
        intro.setWordWrap(True)
        intro.setTextFormat(intro.textFormat().RichText)
        self.hostname_edit = QLineEdit(f"{app.name}.{zone_name}")
        self.hostname_edit.setAccessibleName("Public hostname")
        self.port_box = QComboBox()
        self.port_box.setEditable(True)
        self.port_box.setAccessibleName("Local port of the app on the Pi")
        self.port_box.lineEdit().setPlaceholderText("2567")
        self.hint = QLabel(f"Looking for the port {app.name} listens on (read-only)…")
        self.hint.setWordWrap(True)
        self.error = QLabel("")
        self.error.setObjectName("banner")
        self.error.setVisible(False)

        form = QFormLayout()
        form.addRow("Public hostname", self.hostname_edit)
        form.addRow("Port on the Pi", self.port_box)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Review changes…")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        for item in (intro, self.hint):
            layout.addWidget(item)
        layout.addLayout(form)
        layout.addWidget(self.error)
        layout.addWidget(buttons)

        submit(
            lambda: detect_ports(host, app, transport),
            self._on_ports,
            lambda msg: self.hint.setText(
                f"Could not look on the Pi: {msg}. Type the port."
            ),
        )

    @Slot(object)
    def _on_ports(self, ports: list[int]) -> None:
        if not ports:
            self.hint.setText(
                "No listening port found (is the app running?). Type the port the app uses."
            )
            return
        self.port_box.addItems([str(p) for p in ports])
        found = ", ".join(map(str, ports))
        self.hint.setText(f"The app listens on port {found}. Pick one or type another.")

    def _accept(self) -> None:
        try:
            hostname = check_hostname(self.hostname_edit.text())
            port = check_port(self.port_box.currentText())
        except RouteError as exc:
            self.error.setText(str(exc))
            self.error.setVisible(True)
            return
        self.result_value = (hostname, port)
        self.accept()
