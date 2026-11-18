"""“Install app…” and “Install unit…”: clone and build an app, then create its systemd unit."""

from __future__ import annotations

from PySide6.QtCore import Slot
from PySide6.QtWidgets import QDialog, QMessageBox

from raspi import APP_NAME
from raspi.core.models import Host
from raspi.gui.confirm import confirm_change, confirm_with_option
from raspi.gui.install_dialog import InstallAppDialog
from raspi.gui.workers import submit
from raspi.services.install import InstallPlan, install_app, plan_install
from raspi.services.unit import UnitPlan, install_unit, plan_unit


class InstallMixin:
    """Mixed into MainWindow: relies on its `_transport`, `selected`, `selected_host`,
    `model`, `status_label` and `_refresh_host`."""

    def _live_host_of_selection(self) -> Host | None:
        selected = self.selected_host()
        status = self.model.host_status(selected.name) if selected else None
        return status.host if status and status.reachable else None

    def can_install_app(self) -> bool:
        return self._live_host_of_selection() is not None

    def can_install_unit(self) -> bool:
        row = self.selected()
        return bool(row and row.app and row.app.run and self.can_install_app())

    def start_tooltip(self, state: str) -> str:
        if state == "no-unit":
            return "This app has no systemd unit yet: create it with “Install unit…”"
        return "Start (Ctrl+S)"

    def unit_tooltip(self) -> str:
        row = self.selected()
        if not row or not row.app:
            return "Select an app first"
        if not row.app.run:
            return "This app has no run command: open “Install app…” to set it"
        if not self.can_install_app():
            return "The Pi must be reachable"
        return "Create and enable the app's systemd unit (it is not started)"

    # --- clone and build -----------------------------------------------------------
    def install_app_form(self) -> None:
        host = self._live_host_of_selection()
        if host is None:
            return
        row = self.selected()
        existing = row.app if row and row.app and row.app.repo else None
        dialog = InstallAppDialog(host, self, existing)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.result_value:
            return
        app = dialog.result_value
        self.status_label.setText(f"Checking what installing {app.name} would do…")
        submit(
            lambda: (host, plan_install(host, self._transport, app)),
            self._on_install_planned,
            self._install_failed,
        )

    @Slot(object)
    def _on_install_planned(self, payload: tuple[Host, InstallPlan]) -> None:
        host, plan = payload
        title = f"Install {plan.app.name} on {host.name}?"
        script = f"Script run on {host.name}:\n\n{plan.script}"
        with_unit = False
        if plan.unit:
            confirmed, with_unit = self._confirm_unit_too(
                title, plan.steps, plan.unit, script
            )
            if not confirmed:
                return
        elif not self._confirm_change(title, plan.steps, [], script, "Install"):
            return
        self.status_label.setText(
            f"Installing {plan.app.name}… (cloning and building can take several minutes)"
        )
        submit(
            lambda: (
                host,
                install_app(host, self._transport, plan, with_unit=with_unit),
            ),
            self._on_installed,
            self._install_failed,
        )

    @Slot(object)
    def _on_installed(self, payload) -> None:
        host, result = payload
        self.status_label.setText(("✓ " if result.ok else "✕ ") + result.message)
        if result.ok:
            QMessageBox.information(
                self,
                APP_NAME,
                result.message
                + "\n\nNext: declare and push its environment file (Environment…), "
                "then “Install unit…”.",
            )
        else:
            QMessageBox.critical(self, APP_NAME, result.message)
        self._refresh_host(host.name)

    # --- systemd unit --------------------------------------------------------------
    def install_unit_form(self) -> None:
        host = self._live_host_of_selection()
        row = self.selected()
        if host is None or row is None or row.app is None:
            return
        app = row.app
        self.status_label.setText(f"Checking the unit of {app.name}…")
        submit(
            lambda: (host, plan_unit(host, self._transport, app)),
            self._on_unit_planned,
            self._install_failed,
        )

    @Slot(object)
    def _on_unit_planned(self, payload: tuple[Host, UnitPlan]) -> None:
        host, plan = payload
        if not self._confirm_change(
            f"Create the unit of {plan.app.name} on {host.name}?",
            plan.steps,
            plan.warnings,
            f"Unit file:\n\n{plan.text}",
            "Create",
        ):
            return
        self.status_label.setText(f"Creating the unit of {plan.app.name}…")
        submit(
            lambda: (host, install_unit(host, self._transport, plan)),
            self._on_unit_installed,
            self._install_failed,
        )

    @Slot(object)
    def _on_unit_installed(self, payload) -> None:
        host, result = payload
        self.status_label.setText(("✓ " if result.ok else "✕ ") + result.message)
        if not result.ok:
            QMessageBox.critical(self, APP_NAME, result.message)
        self._refresh_host(host.name)

    # --- shared --------------------------------------------------------------------
    def _confirm_change(self, title, steps, warnings, details, button) -> bool:
        if confirm_change(self, title, steps, warnings, details, button):
            return True
        self.status_label.setText("Cancelled, nothing was changed.")
        return False

    def _confirm_unit_too(self, title, steps, unit, script) -> tuple[bool, bool]:
        """Install app changed what the unit says: offer to rewrite it, ticked, never restarting."""
        confirmed, ticked = confirm_with_option(
            self,
            title,
            steps,
            [],
            f"{script}\n\nUnit file (if ticked):\n\n{unit.text}",
            "Install",
            f"Also rewrite the unit {unit.app.unit} (the app is not restarted; "
            "the new command applies at its next start)",
        )
        if not confirmed:
            self.status_label.setText("Cancelled, nothing was changed.")
        return confirmed, ticked

    def _install_failed(self, message: str) -> None:
        self.status_label.setText("Install step failed")
        QMessageBox.critical(self, APP_NAME, message)
