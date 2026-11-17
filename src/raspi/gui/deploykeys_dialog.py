"""“Deploy keys…”: the keys raspi-manager made on a Pi, with check, rotate and delete."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from raspi import APP_NAME
from raspi.core.deploykey_manage import KeyInfo, delete_steps, rotate_steps
from raspi.core.models import AppRef, Host
from raspi.gui import busy
from raspi.gui.workers import submit
from raspi.services.deploykey import (
    KeyListing,
    KeyResult,
    delete_key,
    list_keys,
    rotate_key,
    verify_repo,
)
from raspi.ssh.transport import Transport

COLUMNS = ("App", "Repository", "Fingerprint", "Used by", "GitHub access")
NOT_CHECKED = "– not checked"


class DeployKeysDialog(QDialog):
    def __init__(
        self,
        host: Host,
        apps: tuple[AppRef, ...],
        transport: Transport,
        on_changed: Callable[[], None] = lambda: None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._host, self._transport, self._on_changed = host, transport, on_changed
        self._apps = {a.name: a for a in apps}
        self._keys: list[KeyInfo] = []
        self._after: Callable[[KeyResult], None] | None = None
        self.setWindowTitle(f"Deploy keys - {host.name}")
        self.resize(860, 380)

        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setAccessibleName("Deploy keys on the Pi")
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        for col, width in enumerate((120, 190, 290, 160)):
            self.table.setColumnWidth(col, width)
        self.table.itemSelectionChanged.connect(self._update_buttons)

        self.btn_copy = QPushButton("Copy public key")
        self.btn_check = QPushButton("Check access")
        self.btn_rotate = QPushButton("Rotate…")
        self.btn_delete = QPushButton("Delete…")
        self.btn_reload = QPushButton("Reload")
        close = QPushButton("Close")
        self.btn_copy.clicked.connect(self._copy)
        self.btn_check.clicked.connect(self._check_all)
        self.btn_rotate.clicked.connect(self._rotate)
        self.btn_delete.clicked.connect(self._delete)
        self.btn_reload.clicked.connect(self.reload)
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        for button in (self.btn_copy, self.btn_check, self.btn_rotate, self.btn_delete):
            row.addWidget(button)
        row.addStretch(1)
        row.addWidget(self.btn_reload)
        row.addWidget(close)

        layout = QVBoxLayout(self)
        layout.addWidget(self.hint)
        layout.addWidget(self.table)
        layout.addLayout(row)
        self._update_buttons()
        self.reload()

    # --- list ---------------------------------------------------------------
    def reload(self) -> None:
        self.hint.setText(f"Reading ~/.ssh on {self._host.name} (read-only)…")
        submit(
            lambda: list_keys(self._host, self._transport),
            self._on_listed,
            lambda msg: self.hint.setText(f"Could not read the keys: {msg}"),
        )

    @Slot(object)
    def _on_listed(self, listing: KeyListing) -> None:
        if not listing.ok:
            self.hint.setText(listing.message)
            return
        self._keys = listing.keys or []
        self.table.setRowCount(0)
        for key in self._keys:
            app = self._apps.get(key.name)
            r = self.table.rowCount()
            self.table.insertRow(r)
            used = ("✓ " + app.name if app else "– no app uses it") + (
                "" if key.complete else "  ⚠ incomplete"
            )
            repo = (app.repo if app else None) or "–"
            for col, text in enumerate(
                (key.name, repo, key.fingerprint or "–", used, NOT_CHECKED)
            ):
                self.table.setItem(r, col, QTableWidgetItem(text))
        unused = sum(1 for k in self._keys if k.name not in self._apps)
        self.hint.setText(
            "No deploy key made by raspi-manager on this Pi."
            if not self._keys
            else f"{len(self._keys)} key(s)"
            + (f", {unused} not used by any app" if unused else "")
            + ". “Check access” asks GitHub, per repository, whether the key is accepted."
        )
        self._update_buttons()

    def _current(self) -> KeyInfo | None:
        rows = self.table.selectionModel().selectedRows()
        return self._keys[rows[0].row()] if rows else None

    def _update_buttons(self) -> None:
        key = self._current()
        self.btn_copy.setEnabled(bool(key and key.public_key))
        self.btn_rotate.setEnabled(bool(key and key.has_files))
        self.btn_delete.setEnabled(key is not None)
        self.btn_check.setEnabled(
            any(self._repo_of(k) and k.has_files for k in self._keys)
        )

    def _repo_of(self, key: KeyInfo) -> str:
        app = self._apps.get(key.name)
        return (app.repo if app else None) or ""

    # --- actions ---------------------------------------------------------------
    def _copy(self) -> None:
        key = self._current()
        if key and key.public_key:
            QGuiApplication.clipboard().setText(key.public_key)
            self.hint.setText(f"Public key of {key.name} copied.")

    def _check_all(self) -> None:
        targets = [(k.name, self._repo_of(k)) for k in self._keys if k.has_files]
        targets = [t for t in targets if t[1]]
        self.hint.setText("Asking GitHub through each key (read-only)…")
        submit(
            lambda: [
                (n, verify_repo(self._host, self._transport, n, repo))
                for n, repo in targets
            ],
            self._on_checked,
            lambda msg: self.hint.setText(f"Check failed: {msg}"),
        )

    @Slot(object)
    def _on_checked(self, results: list[tuple[str, KeyResult]]) -> None:
        by_name = dict(results)
        for r, key in enumerate(self._keys):
            res = by_name.get(key.name)
            if res is not None:
                text = ("✓ accepted" if res.ok else "✕ refused") + (
                    "" if res.ok else f" - {res.message}"
                )
                self.table.setItem(r, 4, QTableWidgetItem(text))
        refused = sum(1 for _, res in results if not res.ok)
        self.hint.setText(
            f"Checked {len(results)} key(s): "
            + ("all accepted." if not refused else f"{refused} refused by GitHub.")
        )

    def _confirm(self, title: str, steps: list[str], script: str, verb: str) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(APP_NAME)
        box.setText(title)
        box.setInformativeText(
            "This changes the Pi (only files in ~/.ssh):\n"
            + "\n".join(f"• {s}" for s in steps)
        )
        box.setDetailedText(f"Script run on {self._host.name}:\n\n{script}")
        confirm = box.addButton(verb, QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        box.exec()
        return box.clickedButton() is confirm

    def _warn_if_used(self, key: KeyInfo) -> str:
        app = self._apps.get(key.name)
        if app and app.repo:
            return f"\n\n{app.name} still uses it: updates of {app.repo} fail until a key is on GitHub."
        return ""

    def _rotate(self) -> None:
        key = self._current()
        if key is None or busy.running(self._host.name):
            return
        plan = rotate_key(self._host, self._transport, key.name, dry_run=True)
        if not plan.ok:
            QMessageBox.critical(self, APP_NAME, plan.message)
            return
        if not self._confirm(
            f"Rotate the deploy key of {key.name}?" + self._warn_if_used(key),
            rotate_steps(key.name, self._host.name),
            plan.script,
            "Rotate",
        ):
            return
        self._mutate(
            lambda: rotate_key(self._host, self._transport, key.name),
            lambda res: self._show_new_key(key, res),
        )

    def _delete(self) -> None:
        key = self._current()
        if key is None or busy.running(self._host.name):
            return
        plan = delete_key(self._host, self._transport, key.name, dry_run=True)
        if not plan.ok:
            QMessageBox.critical(self, APP_NAME, plan.message)
            return
        if not self._confirm(
            f"Delete the deploy key of {key.name}?" + self._warn_if_used(key),
            delete_steps(key.name, self._host.name),
            plan.script,
            "Delete",
        ):
            return
        self._mutate(lambda: delete_key(self._host, self._transport, key.name), None)

    def _mutate(self, job, after) -> None:
        if not busy.begin(self._host.name):
            return
        self._after = after
        self.hint.setText("Working on the Pi…")
        submit(job, self._mutated, self._mutate_failed)

    @Slot(object)
    def _mutated(self, res: KeyResult) -> None:
        busy.end(self._host.name)
        self.hint.setText(("✓ " if res.ok else "✕ ") + res.message)
        if not res.ok:
            QMessageBox.critical(self, APP_NAME, res.message)
        elif self._after:
            self._after(res)
        self._on_changed()
        self.reload()

    @Slot(str)
    def _mutate_failed(self, msg: str) -> None:
        busy.end(self._host.name)
        self.hint.setText(f"✕ {msg}")
        QMessageBox.critical(self, APP_NAME, msg)

    def _show_new_key(self, key: KeyInfo, res: KeyResult) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle(APP_NAME)
        box.setText(f"New deploy key of {key.name}")
        box.setInformativeText(
            "On GitHub: repository, Settings, Deploy keys. Add this key (leave “Allow write "
            "access” unticked), then delete the old one there:\n\n" + res.public_key
        )
        copy = box.addButton("Copy key", QMessageBox.ButtonRole.ActionRole)
        box.addButton("Close", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is copy:
            QGuiApplication.clipboard().setText(res.public_key)
