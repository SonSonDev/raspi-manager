"""Several env files per app in the editor: the picker, adding and forgetting a file, switching."""

from __future__ import annotations

from PySide6.QtCore import Slot
from PySide6.QtWidgets import (
    QDialog,
    QMessageBox,
)

from raspi import APP_NAME
from raspi.gui.add_env_dialog import AddEnvFileDialog
from raspi.gui.declare import run_declaration
from raspi.services.declare import declare_env_file, forget_env_file


class EnvFilesMixin:
    """Mixed into EnvDialog: relies on its table, picker, `_app`, `_env`, `_host`, `_transport`,
    `_dirty`, `_update_header`, `_load_current`, `_on_pi`."""

    def _fill_picker(self, select: str | None = None) -> None:
        self.file_picker.blockSignals(True)
        self.file_picker.clear()
        for env in self._app.env_files:
            self.file_picker.addItem(f"{env.label}  —  {env.path}", env.label)
        index = next(
            (i for i, e in enumerate(self._app.env_files) if e.label == select), 0
        )
        self.file_picker.setCurrentIndex(index)
        self.file_picker.blockSignals(False)

    def add_file(self) -> None:
        dialog = AddEnvFileDialog(
            self._host,
            self._app,
            self._transport,
            {e.label for e in self._app.env_files},
            {e.path for e in self._app.env_files},
            self,
            on_pi=self._on_pi,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.result_value:
            return
        label, path = dialog.result_value
        result = run_declaration(
            lambda: declare_env_file(
                self._host, self._app.name, label, path, self._transport, self._on_pi
            )
        )
        if not result.ok or result.app is None:
            QMessageBox.warning(self, APP_NAME, result.message)
            return
        self._app = result.app
        self._fill_picker(self._env.label)
        index = self.file_picker.findData(label)
        if index >= 0:
            self.file_picker.setCurrentIndex(index)  # asks before dropping edits
        self.note.setText(f"Declared {path} as “{label}”. {result.message}")

    def forget_file(self) -> None:
        if (
            QMessageBox.question(
                self,
                APP_NAME,
                f"Stop managing {self._env.path} in raspi?\n"
                "The file on the Pi and your local copy are not deleted.",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        result = run_declaration(
            lambda: forget_env_file(
                self._host,
                self._app.name,
                self._env.label,
                self._transport,
                self._on_pi,
            )
        )
        if not result.ok:
            QMessageBox.warning(self, APP_NAME, result.message)
            return
        self._dirty = False
        if result.app is None or not result.app.env_files:
            self.close()
            return
        self._app = result.app
        self._env = self._app.env_files[0]
        self._fill_picker(self._env.label)
        self._update_header()
        self._load_current()

    @Slot(int)
    def _switch_file(self, index: int) -> None:
        env = self._app.env_files[index]
        if env.label == self._env.label:
            return
        if (
            self._dirty
            and QMessageBox.question(
                self, APP_NAME, "Switch file and discard your unsaved changes?"
            )
            != QMessageBox.StandardButton.Yes
        ):
            self.file_picker.blockSignals(True)
            self.file_picker.setCurrentIndex(
                next(i for i, e in enumerate(self._app.env_files) if e == self._env)
            )
            self.file_picker.blockSignals(False)
            return
        self._env = env
        self._update_header()
        self._load_current()
