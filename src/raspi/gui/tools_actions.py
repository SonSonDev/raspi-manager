"""“Install tools…”: ffmpeg and yt-dlp on the selected Pi."""

from __future__ import annotations

from PySide6.QtCore import Slot
from PySide6.QtWidgets import QDialog, QMessageBox

from raspi import APP_NAME
from raspi.core.models import Host
from raspi.gui import busy
from raspi.gui.confirm import confirm_change
from raspi.gui.tools_dialog import ToolsDialog
from raspi.gui.workers import submit
from raspi.services.tools import (
    ToolsPlan,
    ToolsResult,
    ToolState,
    install_tools,
    plan_tools,
    read_state,
)


class ToolsMixin:
    """Mixed into MainWindow: relies on its `_hosts`, `_transport`, `selected_host`,
    `can_check_pi`, `status_label` and `_refresh_host`."""

    def can_install_tools(self) -> bool:
        return self.can_check_pi()

    def install_tools_form(self) -> None:
        selected = self.selected_host()
        host = self._hosts.get(selected.name) if selected else None
        if host is None:
            return
        if busy.running(host.name):
            QMessageBox.information(self, APP_NAME, busy.MESSAGE.format(host=host.name))
            return
        self.status_label.setText(f"Looking at the tools on {host.name}…")
        submit(
            lambda: (host, read_state(host, self._transport)),
            self._on_tools_state,
            self._tools_failed,
        )

    @Slot(object)
    def _on_tools_state(self, payload: tuple[Host, ToolState]) -> None:
        host, state = payload
        dialog = ToolsDialog(host.name, state, self)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.result_value:
            self.status_label.setText("Cancelled, nothing was changed.")
            return
        wanted = dialog.result_value
        self.status_label.setText("Checking what installing would do…")
        submit(
            lambda: (host, plan_tools(host, self._transport, wanted)),
            self._on_tools_planned,
            self._tools_failed,
        )

    @Slot(object)
    def _on_tools_planned(self, payload: tuple[Host, ToolsPlan]) -> None:
        host, plan = payload
        if plan.nothing_to_do:
            self.status_label.setText("Nothing to install.")
            QMessageBox.information(
                self, APP_NAME, "\n".join(plan.notes) or "Nothing to install."
            )
            return
        if not confirm_change(
            self,
            f"Install tools on {host.name}?",
            plan.steps,
            plan.notes,
            f"Script run on {host.name}:\n\n{plan.script}",
            "Install",
        ):
            self.status_label.setText("Cancelled, nothing was changed.")
            return
        if not busy.begin(host.name):
            QMessageBox.information(self, APP_NAME, busy.MESSAGE.format(host=host.name))
            return
        self.status_label.setText("Installing… (this can take a few minutes)")
        submit(
            lambda: (host, install_tools(host, self._transport, plan)),
            self._on_tools_installed,
            lambda msg, h=host.name: (busy.end(h), self._tools_failed(msg)),
        )

    @Slot(object)
    def _on_tools_installed(self, payload: tuple[Host, ToolsResult]) -> None:
        host, result = payload
        busy.end(host.name)
        self.status_label.setText(("✓ " if result.ok else "✕ ") + result.messages[0])
        box = QMessageBox.information if result.ok else QMessageBox.critical
        box(self, APP_NAME, "\n".join(result.messages))
        self._refresh_host(host.name)

    def _tools_failed(self, message: str) -> None:
        self.status_label.setText("Install tools failed")
        QMessageBox.critical(self, APP_NAME, message)
