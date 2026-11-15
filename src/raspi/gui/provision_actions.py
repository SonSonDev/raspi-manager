"""“Check Pi…”: see what a Pi lacks to run apps, then install the missing part."""

from __future__ import annotations

from PySide6.QtCore import Slot
from PySide6.QtWidgets import QInputDialog, QMessageBox

from raspi import APP_NAME
from raspi.core.models import Host
from raspi.core.runtime import NODE_LINES, RuntimeError_
from raspi.gui import busy
from raspi.gui.workers import submit
from raspi.services.provision import DoctorReport, ProvisionResult, doctor, provision
from raspi.services.runtime import install_node, plan_node


class ProvisionMixin:
    """Mixed into MainWindow: relies on its `_hosts`, `_transport`, `selected_host`,
    `model`, `status_label` and `_refresh_host`."""

    def can_check_pi(self) -> bool:
        host = self.selected_host()
        status = self.model.host_status(host.name) if host else None
        return bool(status and status.reachable)

    def check_pi(self) -> None:
        selected = self.selected_host()
        configured = self._hosts.get(selected.name) if selected else None
        if configured is None:
            return
        self.status_label.setText(f"Checking {configured.name}…")
        submit(
            lambda: (configured, doctor(configured, self._transport)),
            self._on_pi_checked,
            self._pi_check_failed,
        )

    @Slot(object)
    def _on_pi_checked(self, payload: tuple[Host, DoctorReport]) -> None:
        host, report = payload
        if not report.ok:
            self.status_label.setText("Check failed")
            QMessageBox.critical(self, APP_NAME, report.message)
            return
        self.status_label.setText(
            f"{host.name}: "
            + ("ready for apps" if report.fit else "something is missing")
        )
        box = QMessageBox(self)
        box.setIcon(
            QMessageBox.Icon.Information if report.fit else QMessageBox.Icon.Warning
        )
        box.setWindowTitle(APP_NAME)
        box.setText(
            f"{host.name}: " + ("ready for apps." if report.fit else "not ready yet.")
        )
        box.setInformativeText(
            "\n".join(
                f"{'✓' if c.ok else '✕' if c.required else '–'} {c.label}: {c.detail}"
                + ("" if c.ok else f" → {c.fix}")
                for c in report.checks
            )
        )
        install = None
        if report.steps:
            install = box.addButton(
                "Install missing…", QMessageBox.ButtonRole.AcceptRole
            )
        node = None
        by_key = {c.key: c.ok for c in report.checks}
        if not by_key.get("node") and by_key.get("git") and by_key.get("curl"):
            node = box.addButton("Install Node…", QMessageBox.ButtonRole.ActionRole)
        box.addButton("Close", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if install is not None and box.clickedButton() is install:
            self._install_missing(host, report)
        elif node is not None and box.clickedButton() is node:
            self._install_node(host)

    def _install_node(self, host: Host) -> None:
        if self._refuse_if_busy(host):
            return
        major, ok = QInputDialog.getItem(
            self, APP_NAME, "Node version (LTS line):", NODE_LINES, 0, False
        )
        if not ok:
            return
        try:
            steps, script = plan_node(host, self._transport, major)
        except RuntimeError_ as exc:
            QMessageBox.warning(self, APP_NAME, str(exc))
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(APP_NAME)
        box.setText(f"Install Node {major} on {host.name}?")
        box.setInformativeText(
            "This changes the Pi:\n" + "\n".join(f"• {s}" for s in steps)
        )
        box.setDetailedText(f"Script run on {host.name}:\n\n{script}")
        confirm = box.addButton("Install", QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        box.exec()
        if box.clickedButton() is not confirm:
            self.status_label.setText("Cancelled, nothing was changed.")
            return
        if not busy.begin(host.name):
            self._refuse_if_busy(host)
            return
        self.status_label.setText(f"Installing Node {major} on {host.name}…")
        submit(
            lambda: (host, install_node(host, self._transport, major)),
            self._on_node_installed,
            lambda msg, h=host.name: self._busy_failed(h, msg),
        )

    @Slot(object)
    def _on_node_installed(self, payload) -> None:
        host, (ok, message, _) = payload
        busy.end(host.name)
        self.status_label.setText(("✓ " if ok else "✕ ") + message)
        if not ok:
            QMessageBox.critical(self, APP_NAME, message)
        self._refresh_host(host.name)

    def _install_missing(self, host: Host, report: DoctorReport) -> None:
        if self._refuse_if_busy(host):
            return
        preview = provision(host, self._transport, dry_run=True)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(APP_NAME)
        box.setText(f"Install what {host.name} lacks?")
        box.setInformativeText(
            "This changes the Pi:\n" + "\n".join(f"• {s}" for s in report.steps)
        )
        box.setDetailedText(f"Script run on {host.name}:\n\n{preview.script}")
        confirm = box.addButton("Install", QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        box.exec()
        if box.clickedButton() is not confirm:
            self.status_label.setText("Cancelled, nothing was changed.")
            return
        if not busy.begin(host.name):
            self._refuse_if_busy(host)
            return
        self.status_label.setText(
            f"Installing on {host.name}… (this can take a few minutes)"
        )
        submit(
            lambda: (host, provision(host, self._transport)),
            self._on_provisioned,
            lambda msg, h=host.name: self._busy_failed(h, msg),
        )

    @Slot(object)
    def _on_provisioned(self, payload: tuple[Host, ProvisionResult]) -> None:
        host, result = payload
        busy.end(host.name)
        self.status_label.setText(("✓ " if result.ok else "✕ ") + result.message)
        if not result.ok:
            QMessageBox.critical(self, APP_NAME, result.message)
        self._refresh_host(host.name)

    def _refuse_if_busy(self, host: Host) -> bool:
        """True (after saying why) when a change is already running on that Pi."""
        if not busy.running(host.name):
            return False
        QMessageBox.information(self, APP_NAME, busy.MESSAGE.format(host=host.name))
        return True

    def _busy_failed(self, host_name: str, message: str) -> None:
        busy.end(host_name)
        self._pi_check_failed(message)

    def _pi_check_failed(self, message: str) -> None:
        self.status_label.setText("Pi check failed")
        QMessageBox.critical(self, APP_NAME, message)
