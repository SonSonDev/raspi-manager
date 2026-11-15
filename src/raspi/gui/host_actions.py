"""Add / edit / remove a Pi from the main window (hosts.yaml only; nothing is sent to a Pi)."""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QMessageBox

from raspi import APP_NAME
from raspi.config.loader import ConfigError, load_hosts
from raspi.config.writer import add_host, remove_host, update_host
from raspi.core.models import Host
from raspi.gui.add_host_dialog import AddHostDialog


class HostActionsMixin:
    """Mixed into MainWindow: relies on its `_hosts`, `banner`, `setup_panel`,
    `status_label`, `selected_host`, `reload_config`, `refresh` and `_update_buttons`."""

    def _show_banner(self, error: str, hosts: list[Host]) -> None:
        """A config error shows as a banner; with no Pi at all, show the how-to panel."""
        self.banner.setText(error)
        self.banner.setVisible(bool(error))
        self.setup_panel.setVisible(not error and not hosts)

    def reload_config(self) -> bool:
        """Re-read hosts.yaml (edited from the app) and update the table."""
        try:
            hosts = load_hosts()
        except ConfigError as exc:
            QMessageBox.warning(self, APP_NAME, str(exc))
            return False
        self._hosts = {h.name: h for h in hosts}
        self.model.set_hosts(hosts)
        self._show_banner("", hosts)
        return True

    def add_host(self) -> None:
        dialog = AddHostDialog(set(self._hosts), self)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.result_value:
            return
        try:
            add_host(*dialog.result_value)
        except ConfigError as exc:
            QMessageBox.warning(self, APP_NAME, str(exc))
            return
        if self.reload_config():
            self.status_label.setText(f"Added {dialog.result_value[0]}")
            self.refresh()

    def edit_host(self) -> None:
        host = self.selected_host()
        if not host:
            return
        dialog = AddHostDialog(set(self._hosts), self, editing=host)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.result_value:
            return
        try:
            update_host(*dialog.result_value)
        except ConfigError as exc:
            QMessageBox.warning(self, APP_NAME, str(exc))
            return
        if self.reload_config():
            self.status_label.setText(f"Updated {host.name}")
            self.refresh()

    def _confirm_remove_host(self, host: Host) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(APP_NAME)
        box.setText(f"Remove {host.name} from raspi-manager?")
        apps = ", ".join(a.name for a in host.apps)
        box.setInformativeText(
            (
                f"Its {len(host.apps)} app(s) will be forgotten too: {apps}.\n"
                if apps
                else ""
            )
            + "Only ~/.raspi/hosts.yaml changes (a .bak copy is kept). The Pi, its "
            "apps and the local environment copies are not touched."
        )
        confirm = box.addButton("Remove", QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        box.exec()
        return box.clickedButton() is confirm

    def remove_host(self) -> None:
        host = self.selected_host()
        if not host or not self._confirm_remove_host(host):
            return
        try:
            remove_host(host.name)
        except ConfigError as exc:
            QMessageBox.warning(self, APP_NAME, str(exc))
            return
        if self.reload_config():
            self.status_label.setText(f"Removed {host.name}")
            self._update_buttons()
