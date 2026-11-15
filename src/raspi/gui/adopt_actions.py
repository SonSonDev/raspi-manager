"""“Scan apps…”: find what a Pi already runs and add it to the Pi's app inventory."""

from __future__ import annotations

from PySide6.QtCore import Slot
from PySide6.QtWidgets import QDialog, QMessageBox

from raspi import APP_NAME
from raspi.core.models import AppRef, Host
from raspi.gui.adopt_dialog import AdoptDialog
from raspi.gui.workers import submit
from raspi.services.adopt import add_apps
from raspi.services.declare import DeclareResult


class AdoptMixin:
    """Mixed into MainWindow: relies on its `_hosts`, `_transport`, `selected_host`,
    `model`, `status_label`, `_refresh_host` and `_update_buttons`."""

    def can_scan(self) -> bool:
        host = self.selected_host()
        status = self.model.host_status(host.name) if host else None
        return bool(status and status.reachable)

    def scan_apps(self) -> None:
        selected = self.selected_host()
        configured = self._hosts.get(selected.name) if selected else None
        if selected is None or configured is None:
            return
        dialog = AdoptDialog(selected, self._transport, self)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.result_value:
            return
        apps = dialog.result_value
        if not self._confirm_add(configured, apps):
            return
        self.status_label.setText(f"Adding {len(apps)} app(s) to {configured.name}…")
        submit(
            lambda: (configured, add_apps(configured, apps, self._transport)),
            self._on_apps_added,
            lambda msg: self._add_failed(msg),
        )

    def _confirm_add(self, host: Host, apps: tuple[AppRef, ...]) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(APP_NAME)
        box.setText(f"Add {len(apps)} app(s) to the inventory of {host.name}?")
        box.setInformativeText(
            "Writes ~/.raspi/apps.yaml on the Pi (mode 600, names and paths only; the "
            "previous copy is kept as apps.yaml.bak). The services themselves are "
            "not touched."
        )
        box.setDetailedText(
            "\n".join(f"{a.name}  ({a.unit})  {a.path or '-'}" for a in apps)
        )
        confirm = box.addButton("Add to the Pi", QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        box.exec()
        return box.clickedButton() is confirm

    @Slot(object)
    def _on_apps_added(self, payload: tuple[Host, DeclareResult]) -> None:
        host, result = payload
        self.status_label.setText(("✓ " if result.ok else "✕ ") + result.message)
        if not result.ok:
            QMessageBox.critical(self, APP_NAME, result.message)
        self._refresh_host(host.name)

    def _add_failed(self, message: str) -> None:
        self.status_label.setText(f"Add failed: {message}")
        QMessageBox.critical(self, APP_NAME, f"Add failed: {message}")
