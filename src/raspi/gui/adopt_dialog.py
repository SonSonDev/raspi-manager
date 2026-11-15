"""Scan a Pi for services it already runs and pick the ones raspi-manager should manage."""

from __future__ import annotations

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from raspi.core.models import AppRef, Host
from raspi.gui.workers import submit
from raspi.services.adopt import ScanResult, candidate_to_app, scan_host
from raspi.ssh.transport import Transport

COLUMNS = ("Manage", "App", "State", "Folder", "Command")


class AdoptDialog(QDialog):
    """On accept, `result_value` holds the apps ticked by the user."""

    def __init__(self, host: Host, transport: Transport, parent=None) -> None:
        super().__init__(parent)
        self.result_value: tuple[AppRef, ...] = ()
        self._apps: list[AppRef | None] = []
        self.setWindowTitle(f"Scan apps - {host.name}")
        self.resize(820, 360)

        self.hint = QLabel(
            f"Looking for services run by {host.user} on {host.name} (read-only)…"
        )
        self.hint.setWordWrap(True)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setAccessibleName("Services found on the Pi")
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        for col, width in enumerate((70, 150, 80, 260)):
            self.table.setColumnWidth(col, width)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.add_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.add_button.setText("Add selected…")
        self.add_button.setEnabled(False)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self.hint)
        layout.addWidget(self.table)
        layout.addWidget(self.buttons)

        submit(
            lambda: scan_host(host, transport),
            self._on_scanned,
            lambda msg: self.hint.setText(f"Scan failed: {msg}"),
        )

    @Slot(object)
    def _on_scanned(self, scan: ScanResult) -> None:
        if not scan.ok:
            self.hint.setText(scan.message)
            return
        if not scan.candidates:
            self.hint.setText("No other service of this user found: nothing to add.")
            return
        self.hint.setText(
            f"{len(scan.candidates)} service(s) not managed yet. Tick the ones to manage: "
            "they are added to the app inventory; nothing on the Pi is changed."
        )
        for c in scan.candidates:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self._apps.append(candidate_to_app(c))
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            check.setCheckState(Qt.CheckState.Unchecked)
            self.table.setItem(row, 0, check)
            for col, text in enumerate(
                (c.name, c.state, c.repo or c.workdir, c.command), start=1
            ):
                self.table.setItem(row, col, QTableWidgetItem(text))
        self.add_button.setEnabled(True)

    def _accept(self) -> None:
        picked = tuple(
            app
            for row, app in enumerate(self._apps)
            if app and self.table.item(row, 0).checkState() == Qt.CheckState.Checked
        )
        if not picked:
            self.hint.setText("Tick at least one service, or cancel.")
            return
        self.result_value = picked
        self.accept()
