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

from raspi import APP_NAME
from raspi.config import envstore
from raspi.core.env import diff_env
from raspi.core.models import AppRef, EnvFile, Host
from raspi.gui.env_files import EnvFilesMixin
from raspi.gui.env_push import EnvPushMixin
from raspi.gui.env_template import EnvTemplateMixin
from raspi.gui.workers import submit
from raspi.services.env import RemoteEnv, read_remote
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


class EnvDialog(EnvFilesMixin, EnvPushMixin, EnvTemplateMixin, QDialog):
    def __init__(
        self,
        host: Host,
        app: AppRef,
        transport: Transport,
        parent=None,
        on_pi: bool = False,
    ) -> None:
        super().__init__(parent)
        assert app.env_files
        self._host, self._app, self._transport = host, app, transport
        self._on_pi = on_pi  # the app list lives in the Pi's apps.yaml, not hosts.yaml
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
        self.btn_reset = QPushButton("Reset from template…")
        self.btn_reset.setToolTip(
            "Replace the variables here by those of .env.example (nothing is pushed)"
        )
        self.btn_reset.clicked.connect(self.reset_from_template)
        self.btn_save = QPushButton("Save locally")
        self.btn_save.clicked.connect(self.save_local)
        self.btn_push = QPushButton("Push to Pi")
        self.btn_push.clicked.connect(lambda: self.push_to_pi(restart=False))
        self.btn_push_restart = QPushButton("Push and restart")
        self.btn_push_restart.clicked.connect(lambda: self.push_to_pi(restart=True))
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        buttons = QHBoxLayout()
        for w in (self.btn_import, self.btn_reset, self.btn_save):
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
        self._tpl_offer(remote)

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
        empty = self._tpl_empty_count()
        blank = f" · {empty} without a value" if empty else ""
        self.sync_label.setText(
            f"{self.table.rowCount()} variable(s) here — {state}{unsaved}{blank}"
        )

    # --- actions -----------------------------------------------------------------------
    def closeEvent(self, event) -> None:
        if (
            self._dirty
            and QMessageBox.question(
                self, APP_NAME, "Close without saving your changes?"
            )
            != QMessageBox.StandardButton.Yes
        ):
            event.ignore()
            return
        super().closeEvent(event)
