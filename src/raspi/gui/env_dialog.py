"""Environment-variable editor for one app. Values are masked until the user asks to see them."""

from __future__ import annotations

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from raspi.config import envstore
from raspi.config.loader import ConfigError, load_hosts
from raspi.config.writer import add_env_file, remove_env_file
from raspi.core.env import EnvError, check_pair, diff_env
from raspi.core.models import AppRef, EnvFile, Host
from raspi.gui.add_env_dialog import AddEnvFileDialog
from raspi.gui.workers import submit
from raspi.services.actions import run_action
from raspi.services.env import RemoteEnv, push, read_remote
from raspi.ssh.transport import Transport

MASK = "••••••••"
REAL = (
    Qt.ItemDataRole.UserRole
)  # the real value lives here, display text may be the mask


class _ValueDelegate(QStyledItemDelegate):
    def __init__(self, dialog: EnvDialog) -> None:
        super().__init__(dialog)
        self._dialog = dialog

    def createEditor(self, parent, option, index):
        editor = QLineEdit(parent)
        mode = (
            QLineEdit.EchoMode.Normal
            if self._dialog.show_values.isChecked()
            else QLineEdit.EchoMode.Password
        )
        editor.setEchoMode(mode)
        return editor

    def setEditorData(self, editor, index):
        editor.setText(index.data(REAL) or "")

    def setModelData(self, editor, model, index):
        model.setData(index, editor.text(), REAL)
        self._dialog.refresh_display(index.row())
        self._dialog.mark_dirty()


class EnvDialog(QDialog):
    def __init__(
        self, host: Host, app: AppRef, transport: Transport, parent=None
    ) -> None:
        super().__init__(parent)
        assert app.env_files
        self._host, self._app, self._transport = host, app, transport
        self._env: EnvFile = app.env_files[0]
        self._remote: RemoteEnv | None = None
        self._dirty = False
        self.setWindowTitle(f"Environment - {app.name} on {host.name}")
        self.resize(720, 520)

        self.header = QLabel()
        self.header.setTextFormat(Qt.TextFormat.RichText)
        self.file_picker = QComboBox()
        self.file_picker.setAccessibleName("Environment file")
        self._fill_picker()
        self.file_picker.currentIndexChanged.connect(self._switch_file)
        self.btn_add_file = QPushButton("Add file…")
        self.btn_add_file.setToolTip("Declare another environment file for this app")
        self.btn_add_file.clicked.connect(self.add_file)
        self.btn_forget = QPushButton("Forget file")
        self.btn_forget.setToolTip(
            "Stop managing this file in raspi (the file on the Pi is not touched)"
        )
        self.btn_forget.clicked.connect(self.forget_file)
        file_row = QHBoxLayout()
        file_row.addWidget(self.file_picker, 1)
        file_row.addWidget(self.btn_add_file)
        file_row.addWidget(self.btn_forget)
        self._update_header()
        self.sync_label = QLabel("Checking the Pi…")
        self.sync_label.setAccessibleName("Synchronisation state")

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Variable", "Value"])
        self.table.setAccessibleName("Environment variables")
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 240)
        self.table.verticalHeader().hide()
        self.table.setItemDelegateForColumn(1, _ValueDelegate(self))

        self.show_values = QCheckBox("Show values")
        self.show_values.toggled.connect(self._refresh_all_display)
        add = QPushButton("Add variable")
        add.clicked.connect(self.add_row)
        remove = QPushButton("Remove")
        remove.clicked.connect(self.remove_selected)
        tools = QHBoxLayout()
        for w in (add, remove):
            tools.addWidget(w)
        tools.addStretch()
        tools.addWidget(self.show_values)

        self.btn_import = QPushButton("Import from Pi…")
        self.btn_import.clicked.connect(self.import_from_pi)
        self.btn_save = QPushButton("Save locally")
        self.btn_save.clicked.connect(self.save_local)
        self.btn_push = QPushButton("Push to Pi")
        self.btn_push.clicked.connect(lambda: self.push_to_pi(restart=False))
        self.btn_push_restart = QPushButton("Push and restart")
        self.btn_push_restart.clicked.connect(lambda: self.push_to_pi(restart=True))
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        buttons = QHBoxLayout()
        for w in (self.btn_import, self.btn_save):
            buttons.addWidget(w)
        buttons.addStretch()
        for w in (self.btn_push, self.btn_push_restart, close):
            buttons.addWidget(w)

        self.note = QLabel("")
        self.note.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(self.header)
        layout.addLayout(file_row)
        layout.addWidget(self.sync_label)
        layout.addLayout(tools)
        layout.addWidget(self.table)
        layout.addWidget(self.note)
        layout.addLayout(buttons)

        self._load_current()

    # --- which file ----------------------------------------------------------------
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

    def _reload_app(self) -> bool:
        """Re-read hosts.yaml after it was edited; False if the app disappeared."""
        try:
            for host in load_hosts():
                if host.name == self._host.name:
                    for app in host.apps:
                        if app.name == self._app.name:
                            self._app = app
                            return True
        except ConfigError as exc:
            QMessageBox.warning(self, "raspi", str(exc))
        return False

    def add_file(self) -> None:
        dialog = AddEnvFileDialog(
            self._host,
            self._app,
            self._transport,
            {e.label for e in self._app.env_files},
            {e.path for e in self._app.env_files},
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.result_value:
            return
        label, path = dialog.result_value
        try:
            add_env_file(self._host.name, self._app.name, label, path)
        except ConfigError as exc:
            QMessageBox.warning(self, "raspi", str(exc))
            return
        if self._reload_app():
            self._fill_picker(self._env.label)
            index = self.file_picker.findData(label)
            if index >= 0:
                self.file_picker.setCurrentIndex(index)  # asks before dropping edits
        self.note.setText(
            f"Declared {path} as “{label}” (saved in ~/.raspi/hosts.yaml)."
        )

    def forget_file(self) -> None:
        if (
            QMessageBox.question(
                self,
                "raspi",
                f"Stop managing {self._env.path} in raspi?\n"
                "The file on the Pi and your local copy are not deleted.",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            remove_env_file(self._host.name, self._app.name, self._env.label)
        except ConfigError as exc:
            QMessageBox.warning(self, "raspi", str(exc))
            return
        self._dirty = False
        if not self._reload_app() or not self._app.env_files:
            self.close()
            return
        self._env = self._app.env_files[0]
        self._fill_picker(self._env.label)
        self._update_header()
        self._load_current()

    def _update_header(self) -> None:
        self.header.setText(
            f"<b>{self._app.name}</b> on {self._host.name}<br><code>{self._env.path}</code>"
        )

    def _load_current(self) -> None:
        local = envstore.load_local(self._host.name, self._app.name, self._env.label)
        self.table.setRowCount(0)
        if local is not None:
            self._fill(local)
            self.note.setText("")
        else:
            self.note.setText(
                "No local copy yet. Use “Import from Pi…” to start from the current file, or add variables."
            )
        self._dirty = False
        self._remote = None
        self.sync_label.setText("Checking the Pi…")
        self._check_remote()

    @Slot(int)
    def _switch_file(self, index: int) -> None:
        env = self._app.env_files[index]
        if env.label == self._env.label:
            return
        if (
            self._dirty
            and QMessageBox.question(
                self, "raspi", "Switch file and discard your unsaved changes?"
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

    # --- table helpers ----------------------------------------------------------
    def _fill(self, values: dict[str, str]) -> None:
        self.table.setRowCount(0)
        for key, value in values.items():
            self._append(key, value)

    def _append(self, key: str, value: str) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        key_item = QTableWidgetItem(key)
        value_item = QTableWidgetItem()
        value_item.setData(REAL, value)
        self.table.setItem(row, 0, key_item)
        self.table.setItem(row, 1, value_item)
        self.refresh_display(row)

    def refresh_display(self, row: int) -> None:
        item = self.table.item(row, 1)
        real = item.data(REAL) or ""
        item.setText(real if self.show_values.isChecked() else (MASK if real else ""))

    @Slot()
    def _refresh_all_display(self, *_: object) -> None:
        for row in range(self.table.rowCount()):
            self.refresh_display(row)

    def values(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for row in range(self.table.rowCount()):
            key = self.table.item(row, 0).text().strip()
            if key:
                out[key] = self.table.item(row, 1).data(REAL) or ""
        return out

    def mark_dirty(self) -> None:
        self._dirty = True
        self._update_sync()

    def add_row(self) -> None:
        self._append("NEW_VARIABLE", "")
        self.table.setCurrentCell(self.table.rowCount() - 1, 0)
        self.table.editItem(self.table.item(self.table.rowCount() - 1, 0))
        self.mark_dirty()

    def remove_selected(self) -> None:
        for index in sorted(
            {i.row() for i in self.table.selectedIndexes()}, reverse=True
        ):
            self.table.removeRow(index)
        self.mark_dirty()

    # --- remote state ---------------------------------------------------------------
    def _check_remote(self) -> None:
        host, env, transport = self._host, self._env, self._transport
        submit(
            lambda: (env.label, read_remote(host, env, transport)),
            self._on_remote,
            self._on_remote_failed,
        )

    @Slot(object)
    def _on_remote(self, payload: tuple[str, RemoteEnv]) -> None:
        label, remote = payload
        if label != self._env.label:
            return  # the user switched to another file meanwhile
        self._remote = remote
        if not remote.ok:
            self.sync_label.setText(f"Could not read the Pi: {remote.error}")
            return
        if remote.parsed and remote.parsed.warnings:
            self.note.setText(
                "Warnings on the Pi file: " + "; ".join(remote.parsed.warnings[:5])
            )
        self._update_sync()

    def _on_remote_failed(self, message: str) -> None:
        self.sync_label.setText(f"Could not read the Pi: {message}")

    def _update_sync(self) -> None:
        remote = self._remote
        if remote is None or not remote.ok or remote.parsed is None:
            return
        local_missing = (
            not self.table.rowCount()
            and envstore.load_local(self._host.name, self._app.name, self._env.label)
            is None
        )
        if not remote.exists:
            state = "the file does not exist on the Pi yet"
        elif local_missing:
            state = f"{len(remote.parsed.values)} variable(s) on the Pi, no local copy"
        else:
            d = diff_env(remote.parsed.values, self.values())
            state = (
                "same as the Pi"
                if d.empty
                else f"differs from the Pi ({d.summary()}: added/changed/removed)"
            )
        unsaved = " · unsaved changes" if self._dirty else ""
        self.sync_label.setText(
            f"{self.table.rowCount()} variable(s) here — {state}{unsaved}"
        )

    # --- actions -----------------------------------------------------------------------
    def save_local(self) -> bool:
        try:
            for key, value in self.values().items():
                check_pair(key, value)
            envstore.save_local(
                self._host.name, self._app.name, self.values(), self._env.label
            )
        except EnvError as exc:
            QMessageBox.warning(self, "raspi", str(exc))
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
                self, "raspi", "There is no readable file on the Pi to import."
            )
            return
        if (
            self.table.rowCount()
            and QMessageBox.question(
                self,
                "raspi",
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
            QMessageBox.warning(self, "raspi", str(exc))
            return
        if remote is None or not remote.ok:
            QMessageBox.warning(
                self,
                "raspi",
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
        lines.append("A backup of the current file is kept next to it (.raspi-bak).")
        if restart:
            lines.append(f"Then restarts {self._app.name}.")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("raspi")
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

        def job():
            pushed = push(host, env, values, transport)
            restarted = (
                run_action(host, app, "restart", transport)
                if restart and pushed.ok
                else None
            )
            return pushed, restarted

        submit(job, self._on_pushed, self._on_push_failed)

    @Slot(object)
    def _on_pushed(self, payload) -> None:
        pushed, restarted = payload
        self._set_busy(False)
        if not pushed.ok:
            QMessageBox.critical(self, "raspi", f"Push failed: {pushed.message}")
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
        QMessageBox.critical(self, "raspi", f"Push failed: {message}")

    def _set_busy(self, busy: bool) -> None:
        for b in (self.btn_import, self.btn_save, self.btn_push, self.btn_push_restart):
            b.setEnabled(not busy)

    def closeEvent(self, event) -> None:
        if (
            self._dirty
            and QMessageBox.question(
                self, "raspi", "Close without saving your changes?"
            )
            != QMessageBox.StandardButton.Yes
        ):
            event.ignore()
            return
        super().closeEvent(event)
