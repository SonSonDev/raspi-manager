"""“Environment…” entry point of the main window (declare the first file, then edit)."""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QMessageBox

from raspi import APP_NAME
from raspi.gui.add_env_dialog import AddEnvFileDialog
from raspi.gui.declare import run_declaration
from raspi.gui.env_dialog import EnvDialog
from raspi.services.declare import declare_env_file


class EnvActionsMixin:
    """Mixed into MainWindow: relies on its `selected`, `model`, `_transport`,
    `reload_config`, `select_key`, `_update_buttons` and `_refresh_host`."""

    def _apps_live_on_pi(self, host_name: str) -> bool:
        status = self.model.host_status(host_name)
        return bool(status and status.inventory == "pi")

    def edit_env(self) -> None:
        row = self.selected()
        if not row or not row.app:
            return
        key, app, host = row.key, row.app, row.host
        on_pi = self._apps_live_on_pi(host.name)
        if not app.env_files:  # nothing declared yet: start by declaring a file
            dialog = AddEnvFileDialog(
                host, app, self._transport, set(), set(), self, on_pi=on_pi
            )
            if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.result_value:
                return
            label, path = dialog.result_value
            result = run_declaration(
                lambda: declare_env_file(
                    host, app.name, label, path, self._transport, on_pi
                )
            )
            if not result.ok or result.app is None:
                QMessageBox.warning(self, APP_NAME, result.message)
                return
            app = result.app
        EnvDialog(host, app, self._transport, self, on_pi=on_pi).exec()
        self.reload_config()  # the dialog may have declared or forgotten files
        self.select_key(key)
        self._update_buttons()
        self._refresh_host(key[0])
