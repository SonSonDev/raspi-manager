"""“Update…”: move an installed app to another revision, rebuild, restart, or go back."""

from __future__ import annotations

from PySide6.QtCore import Slot
from PySide6.QtWidgets import QDialog, QMessageBox

from raspi import APP_NAME
from raspi.core.models import Host
from raspi.gui.confirm import confirm_with_options
from raspi.gui.update_dialog import UpdateDialog
from raspi.gui.workers import submit
from raspi.services.update import UpdatePlan, UpdateResult, apply_update, plan_update


class UpdateMixin:
    """Mixed into MainWindow: relies on its `_transport`, `selected`, `model`, `status_label`,
    `_refresh_host` and `_confirm_change` (from the install mixin)."""

    def can_update(self) -> bool:
        row = self.selected()
        status = self.model.host_status(row.host.name) if row and row.app else None
        return bool(
            row
            and row.app
            and row.app.repo
            and status
            and status.reachable
            and row.status
            and row.status.rev
        )

    def update_tooltip(self) -> str:
        row = self.selected()
        if not row or not row.app:
            return "Select an app first"
        if not row.app.repo:
            return "This app has no repository declared: it is not updated from here"
        if not (row.status and row.status.rev):
            return "The app is not cloned on the Pi yet: use “Install app…”"
        return "Update to another version, or go back to the previous one"

    def update_app_form(self) -> None:
        row = self.selected()
        if not row or not row.app:
            return
        dialog = UpdateDialog(row.app, row.host.name, self)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.result_value:
            return
        ref, rollback = dialog.result_value
        self._plan_update_for(row, ref, rollback)

    def quick_update(self) -> None:
        """One click: plan the update to the latest of the default branch, then the usual
        confirmation (incoming commits, script) before anything changes."""
        row = self.selected()
        if row and row.app:
            self._plan_update_for(row, "", False)

    def _plan_update_for(self, row, ref: str, rollback: bool) -> None:
        host, app = row.host, row.app
        self.status_label.setText(f"Checking what updating {app.name} would do…")
        submit(
            lambda: (host, plan_update(host, self._transport, app, ref, rollback)),
            self._on_update_planned,
            self._update_failed,
        )

    @Slot(object)
    def _on_update_planned(self, payload: tuple[Host, UpdatePlan]) -> None:
        host, plan = payload
        if plan.nothing_to_do:
            self.status_label.setText("Already up to date.")
            QMessageBox.information(
                self,
                APP_NAME,
                f"{plan.app.name} is already at {plan.new[:7]} and built.",
            )
            return
        details = f"Script run on {host.name}:\n\n{plan.script}"
        if plan.commits:
            details = "Incoming commits:\n" + "\n".join(plan.commits) + "\n\n" + details
        title = (
            f"Go back {plan.app.name} to {plan.new[:7]} on {host.name}?"
            if plan.rollback
            else f"Update {plan.app.name} to {plan.new[:7]} on {host.name}?"
        )
        if not self._confirm_update(title, plan, details):
            return
        self.status_label.setText(
            f"Updating {plan.app.name}… (the build can take several minutes)"
        )
        submit(
            lambda: (host, apply_update(host, self._transport, plan)),
            self._on_updated,
            self._update_failed,
        )

    def _confirm_update(self, title: str, plan: UpdatePlan, details: str) -> bool:
        """One tick box per process that will be restarted and whose rule is `ask`."""
        asked = [p for p in plan.processes if p.ask]
        button = "Go back" if plan.rollback else "Update"
        if not asked:
            return self._confirm_change(
                title, plan.steps, plan.warnings, details, button
            )
        confirmed, ticks = confirm_with_options(
            self,
            title,
            plan.steps,
            plan.warnings,
            details,
            button,
            [f"Restart {p.unit} after the update" for p in asked],
        )
        if not confirmed:
            self.status_label.setText("Cancelled, nothing was changed.")
            return False
        for process, ticked in zip(asked, ticks, strict=True):
            process.restart = ticked
        return True

    @Slot(object)
    def _on_updated(self, payload: tuple[Host, UpdateResult]) -> None:
        host, result = payload
        text = "\n".join(result.messages)
        self.status_label.setText(("✓ " if result.ok else "✕ ") + result.messages[0])
        if result.ok:
            QMessageBox.information(self, APP_NAME, text)
        else:
            QMessageBox.critical(self, APP_NAME, text)
        self._refresh_host(host.name)

    def _update_failed(self, message: str) -> None:
        self.status_label.setText("Update failed")
        QMessageBox.critical(self, APP_NAME, message)
