"""“New app…” and “Continue setup…”: open the setup wizard for a new or half-set-up app."""

from __future__ import annotations

from PySide6.QtWidgets import QDialog

from raspi.core.models import AppRef, Host
from raspi.gui.install_dialog import InstallAppDialog
from raspi.gui.newapp_wizard import NewAppWizard


class NewAppMixin:
    """Mixed into MainWindow: relies on its `_transport`, `selected`, `_live_host_of_selection`
    (install mixin), `can_install_app`, `reload_config` and `_refresh_host`."""

    def can_continue_setup(self) -> bool:
        row = self.selected()
        return bool(row and row.app and row.app.repo and self.can_install_app())

    def new_app(self) -> None:
        host = self._live_host_of_selection()
        if host is None:
            return
        dialog = InstallAppDialog(host, self, wizard=True)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.result_value:
            return
        self._run_setup_wizard(host, dialog.result_value)

    def continue_setup(self) -> None:
        host, row = self._live_host_of_selection(), self.selected()
        if host is None or row is None or row.app is None:
            return
        self._run_setup_wizard(host, row.app)

    def _run_setup_wizard(self, host: Host, app: AppRef) -> None:
        NewAppWizard(host, app, self._transport, self).exec()
        self.reload_config()
        self._refresh_host(host.name)
