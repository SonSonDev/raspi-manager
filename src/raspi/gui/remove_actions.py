"""“Remove app…”: pick what goes, type the name, then remove it from the Pi."""

from __future__ import annotations

from PySide6.QtCore import Slot
from PySide6.QtWidgets import QDialog, QMessageBox

from raspi import APP_NAME
from raspi.core.models import Host
from raspi.gui.remove_dialog import RemoveAppDialog
from raspi.gui.workers import submit
from raspi.services.remove import RemovePlan, RemoveResult, apply_remove, plan_remove


class RemoveActionsMixin:
    """Mixed into MainWindow: relies on its `_transport`, `selected`, `model`, `status_label`,
    `_live_host_of_selection` (install mixin), `reload_config` and `_refresh_host`."""

    def can_remove_app(self) -> bool:
        row = self.selected()
        return bool(row and row.app and self._live_host_of_selection() is not None)

    def remove_app_form(self) -> None:
        row, host = self.selected(), self._live_host_of_selection()
        if row is None or row.app is None or host is None:
            return
        status = self.model.host_status(host.name)
        on_pi = bool(status and status.inventory == "pi")
        app = row.app
        self.status_label.setText(f"Checking what exists for {app.name}…")
        submit(
            lambda: (host, plan_remove(host, self._transport, app, on_pi)),
            self._on_remove_planned,
            self._remove_failed,
        )

    @Slot(object)
    def _on_remove_planned(self, payload: tuple[Host, RemovePlan]) -> None:
        host, plan = payload
        dialog = RemoveAppDialog(plan, host.name, self)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.result_value:
            self.status_label.setText("Cancelled, nothing was removed.")
            return
        chosen = dialog.result_value
        self.status_label.setText(f"Removing {plan.app.name}…")
        submit(
            lambda: (host, apply_remove(host, self._transport, plan, chosen)),
            self._on_removed,
            self._remove_failed,
        )

    @Slot(object)
    def _on_removed(self, payload: tuple[Host, RemoveResult]) -> None:
        host, result = payload
        text = "\n".join(result.messages)
        self.status_label.setText(("✓ " if result.ok else "✕ ") + result.messages[0])
        if result.ok:
            QMessageBox.information(self, APP_NAME, text)
        else:
            QMessageBox.critical(self, APP_NAME, text)
        self.reload_config()
        self._refresh_host(host.name)

    def _remove_failed(self, message: str) -> None:
        self.status_label.setText("Remove failed")
        QMessageBox.critical(self, APP_NAME, message)
