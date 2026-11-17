"""“Move to another device…”: carry an app and its data to another Pi, then start it there."""

from __future__ import annotations

from PySide6.QtCore import Slot
from PySide6.QtWidgets import QInputDialog, QMessageBox

from raspi import APP_NAME
from raspi.gui import busy
from raspi.gui.confirm import confirm_change
from raspi.gui.workers import submit
from raspi.services.move import MovePlan, MoveResult, apply_move, plan_move


class MoveActionsMixin:
    """Mixed into MainWindow: relies on its `_transport`, `selected`, `model`, `status_label`,
    `_live_host_of_selection` (install mixin), `_hosts` and `_refresh_host`."""

    def can_move_app(self) -> bool:
        row = self.selected()
        return bool(
            row
            and row.app
            and self._live_host_of_selection() is not None
            and len(self._hosts) > 1
        )

    def move_app_form(self) -> None:
        row, source = self.selected(), self._live_host_of_selection()
        if row is None or row.app is None or source is None:
            return
        others = [n for n in self._hosts if n != source.name]
        target_name, ok = QInputDialog.getItem(
            self, APP_NAME, f"Move {row.app.name} to which Pi?", others, 0, False
        )
        if not ok:
            return
        status = self.model.host_status(target_name)
        target = status.host if status else self._hosts[target_name]
        app_name = row.app.name
        self.status_label.setText(f"Checking {source.name} and {target_name}…")
        submit(
            lambda: plan_move(source, target, app_name, self._transport),
            self._on_move_planned,
            self._move_refused,
        )

    @Slot(object)
    def _on_move_planned(self, plan: MovePlan) -> None:
        src, dst = plan.source.name, plan.target.name
        if not confirm_change(
            self,
            f"Move {plan.app.name} from {src} to {dst}?",
            plan.steps,
            plan.warnings,
            plan.scripts,
            "Move",
        ):
            self.status_label.setText("Cancelled, nothing was changed.")
            return
        if not busy.begin(src):
            QMessageBox.information(self, APP_NAME, busy.MESSAGE.format(host=src))
            return
        if not busy.begin(dst):
            busy.end(src)
            QMessageBox.information(self, APP_NAME, busy.MESSAGE.format(host=dst))
            return
        self.status_label.setText(
            f"Moving {plan.app.name} to {dst}… (this can take several minutes)"
        )
        submit(
            lambda: (plan, apply_move(plan, self._transport)),
            self._on_moved,
            lambda msg: self._move_failed(msg, (src, dst)),
        )

    @Slot(object)
    def _on_moved(self, payload: tuple[MovePlan, MoveResult]) -> None:
        plan, result = payload
        for host in (plan.source, plan.target):
            busy.end(host.name)
        self.status_label.setText(
            ("✓ " if result.ok else "✕ ") + result.message.splitlines()[0]
        )
        box = QMessageBox.information if result.ok else QMessageBox.critical
        box(self, APP_NAME, result.message)
        for host in (plan.source, plan.target):
            self._refresh_host(host.name)

    def _move_failed(self, message: str, hosts: tuple[str, ...]) -> None:
        for name in hosts:
            busy.end(name)
        self.status_label.setText("Move failed")
        QMessageBox.critical(
            self,
            APP_NAME,
            f"{message}\n\nCheck both Pis: the app may be stopped on the source.",
        )

    def _move_refused(self, message: str) -> None:
        self.status_label.setText("Move refused")
        QMessageBox.warning(self, APP_NAME, message)
