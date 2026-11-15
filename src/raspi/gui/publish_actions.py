"""“Publish apps to Pi…”: write the hosts.yaml apps to the Pi's own ~/.raspi/apps.yaml."""

from __future__ import annotations

from PySide6.QtCore import Slot
from PySide6.QtWidgets import QMessageBox

from raspi import APP_NAME
from raspi.core.models import Host
from raspi.gui.workers import submit
from raspi.services.inventory import PublishPlan, PublishResult, plan_publish, publish


class PublishMixin:
    """Mixed into MainWindow: relies on its `_hosts`, `_transport`, `selected_host`,
    `model`, `status_label`, `_refresh_host` and `_update_buttons`."""

    def _publishable_host(self) -> Host | None:
        """The configured host of the selection when its apps are still only on the PC."""
        selected = self.selected_host()
        if selected is None:
            return None
        host = self._hosts.get(selected.name)
        status = self.model.host_status(selected.name)
        if host is None or not host.apps or status is None:
            return None
        return host if status.reachable and status.inventory == "pc" else None

    def can_publish(self) -> bool:
        return self._publishable_host() is not None

    def publish_apps(self) -> None:
        host = self._publishable_host()
        if host is None:
            return
        self.status_label.setText(f"Checking {host.name}…")
        submit(
            lambda: (host, plan_publish(host, self._transport)),
            self._on_publish_planned,
            lambda msg: self._publish_failed(host.name, msg),
        )

    @Slot(object)
    def _on_publish_planned(self, payload: tuple[Host, PublishPlan]) -> None:
        host, plan = payload
        if not plan.ok:
            self.status_label.setText(plan.message)
            QMessageBox.information(self, APP_NAME, plan.message)
            return
        if not self._confirm_publish(host, plan):
            self.status_label.setText("Publish cancelled")
            return
        self.status_label.setText(f"Writing the inventory to {host.name}…")
        submit(
            lambda: (host, publish(host, plan, self._transport)),
            self._on_published,
            lambda msg: self._publish_failed(host.name, msg),
        )

    def _confirm_publish(self, host: Host, plan: PublishPlan) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(APP_NAME)
        box.setText(f"Write the app inventory to {host.name}?")
        box.setInformativeText(
            f"Creates {plan.path} on the Pi (mode 600, names and paths only, no "
            "variable values). From then on the Pi's file is the source of truth "
            "for its apps; hosts.yaml keeps only the Pi itself."
        )
        box.setDetailedText(plan.content)
        confirm = box.addButton("Write to the Pi", QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        box.exec()
        return box.clickedButton() is confirm

    @Slot(object)
    def _on_published(self, payload: tuple[Host, PublishResult]) -> None:
        host, result = payload
        self.status_label.setText(("✓ " if result.ok else "✕ ") + result.message)
        if not result.ok:
            QMessageBox.critical(self, APP_NAME, result.message)
        self._refresh_host(host.name)

    def _publish_failed(self, host_name: str, message: str) -> None:
        self.status_label.setText(f"Publish failed for {host_name}: {message}")
        QMessageBox.critical(self, APP_NAME, f"Publish failed: {message}")
