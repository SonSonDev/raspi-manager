"""Main window: live fleet table, per-app actions, logs panel. All SSH runs off the GUI thread."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QItemSelectionModel, Qt, QTimer, Slot
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDockWidget,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QTableView,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from raspi import APP_NAME
from raspi.config.loader import ConfigError, load_hosts
from raspi.config.writer import add_env_file
from raspi.core.models import Host, HostStatus
from raspi.gui.add_env_dialog import AddEnvFileDialog
from raspi.gui.env_dialog import EnvDialog
from raspi.gui.fleet_model import FleetModel, Row
from raspi.gui.theme import Tokens
from raspi.gui.workers import submit
from raspi.services.actions import ActionResult, read_logs, run_action
from raspi.services.status import collect_host
from raspi.ssh.transport import Transport

REFRESH_MS = 10_000
VERB = {"start": "starting", "stop": "stopping", "restart": "restarting"}


class MainWindow(QMainWindow):
    def __init__(
        self,
        hosts: list[Host],
        transport: Transport,
        tokens: Tokens,
        config_error: str = "",
        auto_refresh: bool = True,
    ) -> None:
        super().__init__()
        self._hosts = {h.name: h for h in hosts}
        self._transport = transport
        self._inflight: set[str] = set()
        self.setWindowTitle(APP_NAME)
        self.resize(1000, 640)

        self.model = FleetModel(hosts, tokens)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setAccessibleName("Apps on your Raspberry Pi")
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setHighlightSections(False)
        self.table.horizontalHeader().setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        for col, width in enumerate((140, 160, 120, 110, 220, 90)):
            self.table.setColumnWidth(col, width)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.selectionModel().selectionChanged.connect(self._update_buttons)
        self.table.doubleClicked.connect(lambda *_: self.show_logs())

        self.banner = QLabel(config_error)
        self.banner.setObjectName("banner")
        self.banner.setWordWrap(True)
        self.banner.setVisible(bool(config_error))
        self.banner.setAccessibleName("Configuration error")

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.banner)
        layout.addWidget(self.table)
        self.setCentralWidget(central)

        self._build_toolbar()
        self._build_logs_dock()
        self.status_label = QLabel("Checking…")
        self.statusBar().addWidget(self.status_label)
        self._update_buttons()

        self.timer = QTimer(self)
        self.timer.setInterval(REFRESH_MS)
        self.timer.timeout.connect(self.refresh)
        if auto_refresh:
            self.timer.start()
            QTimer.singleShot(0, self.refresh)

    # --- layout ---------------------------------------------------------------
    def _button(
        self, text: str, slot, shortcut: str = "", name: str = ""
    ) -> QToolButton:
        button = QToolButton()
        button.setText(text)
        if name:
            button.setObjectName(name)
        action = QAction(text.replace("&", ""), self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
            button.setToolTip(f"{text.replace('&', '')} ({shortcut})")
        action.triggered.connect(slot)
        button.setDefaultAction(action)
        button.setText(text)
        return button

    def _build_toolbar(self) -> None:
        bar = QToolBar("Actions")
        bar.setMovable(False)
        self.addToolBar(bar)
        self.btn_refresh = self._button("Refresh", self.refresh, "F5")
        self.btn_restart = self._button(
            "Restart", lambda: self.act("restart"), "Ctrl+R", "primary"
        )
        self.btn_start = self._button(
            "Start", lambda: self.act("start"), "Ctrl+S", "primary"
        )
        self.btn_stop = self._button("Stop", lambda: self.act("stop"), "", "danger")
        self.btn_logs = self._button("Logs", self.show_logs, "Ctrl+L")
        self.btn_env = self._button("Environment…", self.edit_env, "Ctrl+E")
        for b in (
            self.btn_restart,
            self.btn_start,
            self.btn_stop,
            self.btn_logs,
            self.btn_env,
        ):
            bar.addWidget(b)
        bar.addSeparator()
        bar.addWidget(self.btn_refresh)

    def _build_logs_dock(self) -> None:
        self.logs_view = QPlainTextEdit()
        self.logs_view.setReadOnly(True)
        self.logs_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.logs_view.setAccessibleName("Logs")
        self.logs_dock = QDockWidget("Logs")
        self.logs_dock.setWidget(self.logs_view)
        self.logs_dock.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea)
        self.logs_dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.logs_dock)
        self.logs_dock.hide()
        self._logs_key: tuple[str, str] | None = None

    # --- selection ---------------------------------------------------------------
    def selected(self) -> Row | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        row = self.model.rows[rows[0].row()]
        return row if row.app else None

    def select_key(self, key: tuple[str, str]) -> None:
        r = self.model.row_for_key(key)
        if r >= 0:
            self.table.selectionModel().select(
                self.model.index(r, 0),
                QItemSelectionModel.SelectionFlag.ClearAndSelect
                | QItemSelectionModel.SelectionFlag.Rows,
            )

    @Slot()
    def _update_buttons(self, *_: object) -> None:
        row = self.selected()
        state = (
            self.model.state_at(self.table.selectionModel().selectedRows()[0].row())
            if row
            else ""
        )
        self.btn_restart.setEnabled(state == "running")
        self.btn_stop.setEnabled(state == "running")
        self.btn_start.setEnabled(state in ("stopped", "failed", "unknown"))
        self.btn_logs.setEnabled(bool(row))
        self.btn_env.setEnabled(bool(row and row.app))
        declared = bool(row and row.app and row.app.env_files)
        self.btn_env.setToolTip(
            "Environment variables (Ctrl+E)"
            if declared
            else "Declare the environment file of this app (Ctrl+E)"
        )

    # --- refresh ---------------------------------------------------------------
    @Slot()
    def refresh(self) -> None:
        for host in self._hosts.values():
            if host.name in self._inflight:
                continue
            self._inflight.add(host.name)
            submit(
                lambda h=host: collect_host(h, self._transport),
                self._on_status,
                lambda msg, n=host.name: self._on_status_failed(n, msg),
            )
        self.status_label.setText("Refreshing…")

    @Slot(object)
    def _on_status(self, hs: HostStatus) -> None:
        self._inflight.discard(hs.host.name)
        keys = [r.key for r in self.model.rows]
        selected = self.selected()
        self.model.set_host_status(hs)
        if keys != [r.key for r in self.model.rows] and selected:
            self.select_key(selected.key)
        self._update_buttons()
        if not self._inflight:
            self.status_label.setText(f"Last checked {datetime.now():%H:%M:%S}")

    def _on_status_failed(self, host_name: str, message: str) -> None:
        self._inflight.discard(host_name)
        self.status_label.setText(f"Refresh failed for {host_name}: {message}")

    # --- actions ---------------------------------------------------------------
    def act(self, action: str) -> None:
        row = self.selected()
        if not row or not row.app:
            return
        if action != "start" and not self._confirm(row, action):
            return
        key = row.key
        self.model.set_pending(key, VERB[action])
        self._update_buttons()
        self.status_label.setText(f"{VERB[action].capitalize()} {row.app.name}…")
        host, app = row.host, row.app
        submit(
            lambda: (key, action, run_action(host, app, action, self._transport)),
            self._on_action_done,
            lambda msg, k=key: self._on_action_failed(k, msg),
        )

    def _confirm(self, row: Row, action: str) -> bool:
        assert row.app
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(APP_NAME)
        box.setText(f"{action.capitalize()} {row.app.name} on {row.host.name}?")
        box.setInformativeText(
            f"Runs: sudo systemctl {action} {row.app.unit}\n"
            + (
                "The app will stay down until started again."
                if action == "stop"
                else "The app will be briefly unavailable."
            )
        )
        confirm = box.addButton(action.capitalize(), QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        box.exec()
        return box.clickedButton() is confirm

    @Slot(object)
    def _on_action_done(
        self, payload: tuple[tuple[str, str], str, ActionResult]
    ) -> None:
        key, action, result = payload
        self.model.set_pending(key, None)
        name = key[1]
        if result.ok:
            self.status_label.setText(f"✓ {action} {name}: now {result.state}")
        else:
            self.status_label.setText(f"✕ {action} {name} failed")
            QMessageBox.critical(
                self,
                APP_NAME,
                f"{action.capitalize()} {name} failed:\n{result.message}",
            )
        self._refresh_host(key[0])
        self._update_buttons()

    def _on_action_failed(self, key: tuple[str, str], message: str) -> None:
        self.model.set_pending(key, None)
        self.status_label.setText(f"✕ {key[1]}: {message}")
        QMessageBox.critical(self, APP_NAME, message)
        self._update_buttons()

    def _refresh_host(self, host_name: str) -> None:
        host = self._hosts[host_name]
        if host_name in self._inflight:
            return
        self._inflight.add(host_name)
        submit(
            lambda: collect_host(host, self._transport),
            self._on_status,
            lambda msg: self._on_status_failed(host_name, msg),
        )

    # --- environment ------------------------------------------------------------
    def edit_env(self) -> None:
        row = self.selected()
        if not row or not row.app:
            return
        key, app = row.key, row.app
        if not app.env_files:  # nothing declared yet: start by declaring a file
            dialog = AddEnvFileDialog(
                row.host, app, self._transport, set(), set(), self
            )
            if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.result_value:
                return
            try:
                add_env_file(row.host.name, app.name, *dialog.result_value)
            except ConfigError as exc:
                QMessageBox.warning(self, APP_NAME, str(exc))
                return
            if not self.reload_config():
                return
            self.select_key(key)
            row = self.selected()
            if not row or not row.app or not row.app.env_files:
                return
        EnvDialog(row.host, row.app, self._transport, self).exec()
        self.reload_config()  # the dialog may have declared or forgotten files
        self.select_key(key)
        self._update_buttons()
        self._refresh_host(key[0])

    def reload_config(self) -> bool:
        """Re-read hosts.yaml (edited from the app) and update the table."""
        try:
            hosts = load_hosts()
        except ConfigError as exc:
            QMessageBox.warning(self, APP_NAME, str(exc))
            return False
        self._hosts = {h.name: h for h in hosts}
        self.model.set_hosts(hosts)
        return True

    # --- logs ---------------------------------------------------------------
    def show_logs(self) -> None:
        row = self.selected()
        if not row or not row.app:
            return
        self._logs_key = row.key
        self.logs_dock.setWindowTitle(f"Logs: {row.app.name} on {row.host.name}")
        self.logs_view.setPlainText("Loading…")
        self.logs_dock.show()
        host, app = row.host, row.app
        submit(
            lambda: (row.key, read_logs(host, app, self._transport, 200)),
            self._on_logs,
            lambda msg: self.logs_view.setPlainText(f"✕ {msg}"),
        )

    @Slot(object)
    def _on_logs(self, payload: tuple[tuple[str, str], ActionResult]) -> None:
        key, result = payload
        if key != self._logs_key:
            return  # user moved on to another app
        self.logs_view.setPlainText(
            result.message if result.ok else f"✕ {result.message}"
        )
        bar = self.logs_view.verticalScrollBar()
        bar.setValue(bar.maximum())
