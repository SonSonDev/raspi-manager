"""Saving, importing and pushing the variables of the editor (names only in every message)."""

from __future__ import annotations

from PySide6.QtCore import Slot
from PySide6.QtWidgets import (
    QMessageBox,
)

from raspi import APP_NAME
from raspi.config import envstore
from raspi.core.env import EnvError, check_pair, diff_env
from raspi.core.processes import restart_targets
from raspi.gui.workers import submit
from raspi.services.actions import run_processes
from raspi.services.env import push


class EnvPushMixin:
    """Mixed into EnvDialog: relies on its table, `values`, `_remote`, `_env`, `_host`, `_app`,
    `_transport`, `_dirty`, `note`, `_fill`, `mark_dirty`, `_update_sync`, `_check_remote`, buttons."""

    def save_local(self) -> bool:
        try:
            for key, value in self.values().items():
                check_pair(key, value)
            envstore.save_local(
                self._host.name, self._app.name, self.values(), self._env.label
            )
        except EnvError as exc:
            QMessageBox.warning(self, APP_NAME, str(exc))
            return False
        self._dirty = False
        self._update_sync()
        self.note.setText("Saved to your PC (~/.raspi/env, outside git).")
        return True

    def import_from_pi(self) -> None:
        remote = self._remote
        if (
            remote is None
            or not remote.ok
            or not remote.exists
            or remote.parsed is None
        ):
            QMessageBox.information(
                self, APP_NAME, "There is no readable file on the Pi to import."
            )
            return
        if (
            self.table.rowCount()
            and QMessageBox.question(
                self,
                APP_NAME,
                "Replace the variables shown here with the ones from the Pi?",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self._fill(remote.parsed.values)
        self.mark_dirty()
        self.note.setText(
            "Imported from the Pi. Values stay masked; they are only saved when you click “Save locally”."
        )

    def push_to_pi(self, restart: bool) -> None:
        values = self.values()
        remote = self._remote
        try:
            for key, value in values.items():
                check_pair(key, value)
        except EnvError as exc:
            QMessageBox.warning(self, APP_NAME, str(exc))
            return
        if remote is None or not remote.ok:
            QMessageBox.warning(
                self,
                APP_NAME,
                "The Pi file could not be read, so pushing is blocked. Try again later.",
            )
            return
        d = diff_env(remote.parsed.values if remote.parsed else {}, values)
        lines = [
            f"Write {len(values)} variable(s) to {self._env.path} on {self._host.name}?"
        ]
        for label, names in (
            ("Added", d.added),
            ("Changed", d.changed),
            ("Removed", d.removed),
        ):
            if names:
                lines.append(f"{label}: {', '.join(names)}")
        if d.empty:
            lines.append("Nothing differs from the Pi.")
        lines += self._tpl_empty_lines(values)
        lines.append("A backup of the current file is kept next to it (.raspi-bak).")
        if restart:
            lines += self._restart_lines()
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(APP_NAME)
        box.setText("\n".join(lines))
        go = box.addButton(
            "Push and restart" if restart else "Push", QMessageBox.ButtonRole.AcceptRole
        )
        cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        box.exec()
        if box.clickedButton() is not go or not self.save_local():
            return
        self._set_busy(True)
        host, app, env, transport = self._host, self._app, self._env, self._transport
        targets, _ = restart_targets(app)

        def job():
            pushed = push(host, env, values, transport)
            restarted = (
                run_processes(host, app, "restart", transport, targets)
                if restart and pushed.ok
                else None
            )
            return pushed, restarted

        submit(job, self._on_pushed, self._on_push_failed)

    def _restart_lines(self) -> list[str]:
        """What “Push and restart” will do to each process (a `keep` one is left running)."""
        if not self._app.processes:
            return [f"Then restarts {self._app.name}."]
        targets, kept = restart_targets(self._app)
        lines = []
        if targets:
            lines.append("Then restarts " + ", ".join(p.unit for p in targets) + ".")
        if kept:
            lines.append(
                "Left running with the old values (restart them yourself when ready): "
                + ", ".join(p.unit for p in kept)
                + "."
            )
        return lines

    @Slot(object)
    def _on_pushed(self, payload) -> None:
        pushed, restarted = payload
        self._set_busy(False)
        if not pushed.ok:
            QMessageBox.critical(self, APP_NAME, f"Push failed: {pushed.message}")
            return
        msg = f"Pushed ({pushed.message})."
        if restarted is not None:
            msg += f" Restart: {'ok, now ' + restarted.state if restarted.ok else 'FAILED - ' + restarted.message}"
        elif self._remote is not None:
            msg += " The app keeps its old values until it is restarted."
        self.note.setText(msg)
        self._check_remote()

    def _on_push_failed(self, message: str) -> None:
        self._set_busy(False)
        QMessageBox.critical(self, APP_NAME, f"Push failed: {message}")

    def _set_busy(self, busy: bool) -> None:
        for b in (self.btn_import, self.btn_save, self.btn_push, self.btn_push_restart):
            b.setEnabled(not busy)
