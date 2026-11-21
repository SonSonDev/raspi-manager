"""Main window: live fleet table, per-app actions, logs panel. All SSH runs off the GUI thread."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QItemSelectionModel, Qt, QTimer, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDockWidget,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from raspi import APP_NAME
from raspi.core.models import Host, HostStatus
from raspi.core.requires import missing_lines
from raspi.gui.adopt_actions import AdoptMixin
from raspi.gui.chrome import build_chrome
from raspi.gui.cloudflare_dialog import CloudflareDialog
from raspi.gui.confirm import confirm_change
from raspi.gui.deploykey_actions import DeployKeyMixin
from raspi.gui.env_actions import EnvActionsMixin
from raspi.gui.fleet_model import NEXT_COL, FleetModel, Row
from raspi.gui.host_actions import HostActionsMixin
from raspi.gui.install_actions import InstallMixin
from raspi.gui.move_actions import MoveActionsMixin
from raspi.gui.newapp_actions import NewAppMixin
from raspi.gui.provision_actions import ProvisionMixin
from raspi.gui.publish_actions import PublishMixin
from raspi.gui.remove_actions import RemoveActionsMixin
from raspi.gui.route_actions import RouteMixin
from raspi.gui.setup_panel import SetupPanel
from raspi.gui.theme import Tokens
from raspi.gui.tools_actions import ToolsMixin
from raspi.gui.update_actions import UpdateMixin
from raspi.gui.workers import submit
from raspi.services.actions import ActionResult, read_logs, run_action
from raspi.services.status import collect_host
from raspi.ssh.transport import Transport

REFRESH_MS = 10_000
VERB = {"start": "starting", "stop": "stopping", "restart": "restarting"}


class MainWindow(
    HostActionsMixin,
    PublishMixin,
    AdoptMixin,
    ProvisionMixin,
    DeployKeyMixin,
    InstallMixin,
    NewAppMixin,
    RemoveActionsMixin,
    MoveActionsMixin,
    ToolsMixin,
    UpdateMixin,
    EnvActionsMixin,
    RouteMixin,
    QMainWindow,
):
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
        for col, width in enumerate((130, 150, 110, 240, 100, 190, 80)):
            self.table.setColumnWidth(col, width)
        # the next step is the column that takes the free width, the rest keeps a fixed size
        self.table.horizontalHeader().setSectionResizeMode(
            NEXT_COL, QHeaderView.ResizeMode.Stretch
        )
        self.table.selectionModel().selectionChanged.connect(self._update_buttons)
        self.table.doubleClicked.connect(lambda *_: self.show_logs())

        self.banner = QLabel(config_error)
        self.banner.setObjectName("banner")
        self.banner.setWordWrap(True)
        self.banner.setAccessibleName("Configuration error")
        self.setup_panel = SetupPanel()
        self._show_banner(config_error, hosts)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.banner)
        layout.addWidget(self.setup_panel)
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

    def set_keep_on_top(self, on: bool) -> None:
        if bool(self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) == on:
            return
        was_visible = self.isVisible()
        # changing a window flag hides the window; show it again where it was
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        if was_visible:
            self.show()
        self.btn_on_top.setChecked(on)

    # --- layout ---------------------------------------------------------------
    def _build_toolbar(self) -> None:
        build_chrome(self)

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
        self._logs_key: tuple[str, ...] | None = None

    # --- selection ---------------------------------------------------------------
    def selected(self) -> Row | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        row = self.model.rows[rows[0].row()]
        return row if row.app else None

    def select_key(self, key: tuple[str, ...]) -> None:
        r = self.model.row_for_key(key)
        if r >= 0:
            self.table.selectionModel().select(
                self.model.index(r, 0),
                QItemSelectionModel.SelectionFlag.ClearAndSelect
                | QItemSelectionModel.SelectionFlag.Rows,
            )

    def selected_host(self) -> Host | None:
        """Host of the selected row, also when the Pi has no app yet."""
        rows = self.table.selectionModel().selectedRows()
        return self.model.rows[rows[0].row()].host if rows else None

    @Slot()
    def _update_buttons(self, *_: object) -> None:
        self.btn_edit_host.setEnabled(self.selected_host() is not None)
        self.btn_remove_host.setEnabled(self.selected_host() is not None)
        row = self.selected()
        state = (
            self.model.state_at(self.table.selectionModel().selectedRows()[0].row())
            if row
            else ""
        )
        # a unit that systemd keeps restarting after failures can be stopped (and restarted)
        looping = (
            bool(row) and state == "working" and not self.model.is_pending(row.key)
        )
        self.btn_restart.setEnabled(state == "running" or looping)
        self.btn_stop.setEnabled(state == "running" or looping)
        self.btn_start.setEnabled(state in ("stopped", "failed", "unknown"))
        self.btn_logs.setEnabled(bool(row))
        self.btn_publish.setEnabled(self.can_publish())
        self.btn_scan.setEnabled(self.can_scan())
        self.btn_check.setEnabled(self.can_check_pi())
        self.btn_key.setEnabled(self.can_deploy_key())
        self.btn_keys.setEnabled(self.can_deploy_key())
        self.btn_install.setEnabled(self.can_install_app())
        self.btn_unit.setEnabled(self.can_install_unit())
        self.btn_start.setToolTip(self.start_tooltip(state))
        self.btn_unit.setToolTip(self.unit_tooltip())
        self.btn_update.setEnabled(self.can_update())
        self.btn_quick_update.setEnabled(self.can_update())
        self.btn_update_now.setEnabled(self.can_update())
        self.btn_update_now.setToolTip(
            "Update to the latest version with NO confirmation (Ctrl+Shift+U)"
            if self.can_update()
            else self.update_tooltip()
        )
        self.btn_new.setEnabled(self.can_install_app())
        self.btn_tools.setEnabled(self.can_install_tools())
        self.btn_remove_app.setEnabled(self.can_remove_app())
        self.btn_move.setEnabled(self.can_move_app())
        self.btn_continue.setEnabled(self.can_continue_setup())
        self.btn_update.setToolTip(self.update_tooltip())
        if self.can_update():
            self.btn_quick_update.setToolTip(
                "Update to the latest version of the default branch (Ctrl+U); "
                "you confirm first"
            )
        else:
            self.btn_quick_update.setToolTip(self.update_tooltip())
        self.btn_route.setEnabled(self.can_route())
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
            self.status_label.setText(self.model.status_summary(datetime.now()))

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
        if action == "start" and not self._start_despite_missing(row):
            return
        key = row.key
        self.model.set_pending(key, VERB[action])
        self._update_buttons()
        self.status_label.setText(f"{VERB[action].capitalize()} {row.label}…")
        host, app = row.host, row.target
        submit(
            lambda: (key, action, run_action(host, app, action, self._transport)),
            self._on_action_done,
            lambda msg, k=key: self._on_action_failed(k, msg),
        )

    def _start_despite_missing(self, row: Row) -> bool:
        """A start is direct, unless a declared prerequisite is missing: then ask first."""
        lines = missing_lines(row.app, row.status)
        return not lines or confirm_change(
            self,
            f"Start {row.label} anyway?",
            [f"Run: sudo systemctl start {row.target.unit}"],
            lines,
            "",
            "Start anyway",
        )

    def _confirm(self, row: Row, action: str) -> bool:
        assert row.app
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(APP_NAME)
        box.setText(f"{action.capitalize()} {row.label} on {row.host.name}?")
        box.setInformativeText(
            f"Runs: sudo systemctl {action} {row.target.unit}\n"
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
        self, payload: tuple[tuple[str, ...], str, ActionResult]
    ) -> None:
        key, action, result = payload
        self.model.set_pending(key, None)
        name = " ".join(key[1:])
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

    def _on_action_failed(self, key: tuple[str, ...], message: str) -> None:
        self.model.set_pending(key, None)
        self.status_label.setText(f"✕ {' '.join(key[1:])}: {message}")
        QMessageBox.critical(self, APP_NAME, message)
        self._update_buttons()

    def _refresh_host(self, host_name: str) -> None:
        host = self._hosts.get(host_name)
        if host is None or host_name in self._inflight:
            return  # removed from the config while an action was running
        self._inflight.add(host_name)
        submit(
            lambda: collect_host(host, self._transport),
            self._on_status,
            lambda msg: self._on_status_failed(host_name, msg),
        )

    # --- cloudflare ---------------------------------------------------------------
    def open_cloudflare(self) -> None:
        CloudflareDialog(list(self._hosts.values()), self).exec()

    # --- logs ---------------------------------------------------------------
    def show_logs(self) -> None:
        row = self.selected()
        if not row or not row.app:
            return
        self._logs_key = row.key
        self.logs_dock.setWindowTitle(f"Logs: {row.label} on {row.host.name}")
        self.logs_view.setPlainText("Loading…")
        self.logs_dock.show()
        host, app = row.host, row.target
        submit(
            lambda: (row.key, read_logs(host, app, self._transport, 200)),
            self._on_logs,
            lambda msg: self.logs_view.setPlainText(f"✕ {msg}"),
        )

    @Slot(object)
    def _on_logs(self, payload: tuple[tuple[str, ...], ActionResult]) -> None:
        key, result = payload
        if key != self._logs_key:
            return  # user moved on to another app
        self.logs_view.setPlainText(
            result.message if result.ok else f"✕ {result.message}"
        )
        bar = self.logs_view.verticalScrollBar()
        bar.setValue(bar.maximum())
