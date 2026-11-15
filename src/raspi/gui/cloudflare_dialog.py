"""Cloudflare window: token (Windows Credential Manager), account/zone, tunnels and routes."""

from __future__ import annotations

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from raspi import APP_NAME
from raspi.cloudflare.client import CloudflareClient, CloudflareError
from raspi.config import cloudflare_settings as store
from raspi.core.models import Host
from raspi.core.routes import RouteError
from raspi.gui.workers import submit
from raspi.services import routing


class AddRouteDialog(QDialog):
    """Asks for the Pi, an optional project, the public hostname and the local port.

    `result_value` = (host, hostname, service, project); project "" means the Pi's shared tunnel."""

    def __init__(
        self,
        hosts: list[Host],
        zone_name: str,
        parent=None,
        prefill: tuple[str, str, str, str] | None = None,
    ) -> None:
        """`prefill` = (host, hostname, service, project) turns this into an edit: only the service can change."""
        super().__init__(parent)
        self.result_value: tuple[str, str, str, str] | None = None
        self.setWindowTitle("Edit a route" if prefill else "Add a route")
        self.host_box = QComboBox()
        self.host_box.addItems([h.name for h in hosts])
        self.hostname = QLineEdit()
        self.hostname.setPlaceholderText(f"app.{zone_name or 'example.com'}")
        self.service = QLineEdit("http://localhost:")
        self.project = QLineEdit()
        self.project.setPlaceholderText(
            "optional, e.g. web-game (empty = one tunnel shared by the Pi)"
        )
        self.project.setAccessibleName("Project")
        self.tunnel_label = QLabel("")
        self.tunnel_label.setAccessibleName("Tunnel name")
        if prefill:
            host, hostname, service, project = prefill
            self.host_box.setCurrentText(host)
            self.hostname.setText(hostname)
            self.service.setText(service)
            self.project.setText(project)
            self.host_box.setEnabled(False)
            self.hostname.setReadOnly(True)
            self.project.setReadOnly(True)
        self.host_box.currentTextChanged.connect(self._update_tunnel_label)
        self.project.textChanged.connect(self._update_tunnel_label)
        self._update_tunnel_label()
        form = QFormLayout()
        form.addRow("Pi running the app", self.host_box)
        form.addRow("Project", self.project)
        form.addRow("Tunnel", self.tunnel_label)
        form.addRow("Public hostname", self.hostname)
        form.addRow("Local service", self.service)
        self.error = QLabel("")
        self.error.setObjectName("banner")
        self.error.setVisible(False)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Preview")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel("Nothing changes until you confirm the preview on the next screen.")
        )
        layout.addLayout(form)
        layout.addWidget(self.error)
        layout.addWidget(buttons)

    def _update_tunnel_label(self, *_: object) -> None:
        host, project = self.host_box.currentText(), self.project.text().strip()
        self.tunnel_label.setText(
            routing.tunnel_name_for(host, project or None) if host else ""
        )

    def _accept(self) -> None:
        if not self.host_box.currentText() or not self.hostname.text().strip():
            self.error.setText("Choose a Pi and type the public hostname.")
            self.error.setVisible(True)
            return
        self.result_value = (
            self.host_box.currentText(),
            self.hostname.text(),
            self.service.text(),
            self.project.text().strip(),
        )
        self.accept()


class DnsRecordsDialog(QDialog):
    """Read-only list of the zone's DNS records, filterable; shows which tunnel a CNAME points to."""

    def __init__(
        self,
        client: CloudflareClient,
        settings: store.CloudflareSettings,
        initial_filter: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._entries: list[routing.DnsEntry] = []
        self.setWindowTitle(f"DNS records - {settings.zone_name}")
        self.resize(900, 520)
        self.filter = QLineEdit(initial_filter)
        self.filter.setPlaceholderText("Filter by name, type or target…")
        self.filter.setAccessibleName("Filter DNS records")
        self.filter.textChanged.connect(self._apply_filter)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Name", "Type", "Points to", "Proxied", "Tunnel"]
        )
        self.table.setAccessibleName("DNS records")
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        for col, width in enumerate((250, 70, 330, 70)):
            self.table.setColumnWidth(col, width)
        self.status = QLabel("Reading the DNS records…")
        self.status.setWordWrap(True)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Read-only: nothing is created, changed or deleted from this window."
            )
        )
        layout.addWidget(self.filter)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.status)
        layout.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)
        submit(
            lambda: routing.dns_overview(client, settings),
            self._on_loaded,
            self._on_failed,
        )

    @Slot(object)
    def _on_loaded(self, entries: list[routing.DnsEntry]) -> None:
        self._entries = entries
        self._apply_filter()

    def _on_failed(self, message: str) -> None:
        self.status.setText(message)

    def _apply_filter(self, *_: object) -> None:
        needle = self.filter.text().strip().lower()
        shown = [
            e
            for e in self._entries
            if not needle or needle in f"{e.name} {e.type} {e.content} {e.note}".lower()
        ]
        self.table.setRowCount(0)
        for e in shown:
            row = self.table.rowCount()
            self.table.insertRow(row)
            for col, text in enumerate(
                (e.name, e.type, e.content, "yes" if e.proxied else "no", e.note)
            ):
                self.table.setItem(row, col, QTableWidgetItem(text))
        self.status.setText(f"{len(shown)} of {len(self._entries)} record(s).")


class CloudflareDialog(QDialog):
    def __init__(self, hosts: list[Host], parent=None) -> None:
        super().__init__(parent)
        self._hosts = hosts
        self.setWindowTitle(f"Cloudflare - {APP_NAME}")
        self.resize(820, 560)

        self.token_edit = QLineEdit()
        self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.token_edit.setPlaceholderText(
            "Paste an API token (Account: Cloudflare Tunnel Edit, Zone: DNS Edit)"
        )
        self.token_edit.setAccessibleName("Cloudflare API token")
        self.btn_save_token = QPushButton("Save token")
        self.btn_save_token.clicked.connect(self.save_token)
        self.btn_remove_token = QPushButton("Remove token")
        self.btn_remove_token.clicked.connect(self.remove_token)
        token_row = QHBoxLayout()
        for w in (self.token_edit, self.btn_save_token, self.btn_remove_token):
            token_row.addWidget(w, 1 if w is self.token_edit else 0)
        self.token_state = QLabel("")

        self.zone_name = QLineEdit()
        self.zone_name.setPlaceholderText("example.com")
        self.zone_id = QLineEdit()
        self.account_id = QLineEdit()
        self.btn_lookup = QPushButton("Look up from token")
        self.btn_lookup.setToolTip(
            "Finds the zone and account ID of the zone name above"
        )
        self.btn_lookup.clicked.connect(self.lookup)
        self.btn_save_settings = QPushButton("Save")
        self.btn_save_settings.clicked.connect(self.save_settings)
        form = QFormLayout()
        form.addRow("Zone (your domain)", self.zone_name)
        form.addRow("Zone ID", self.zone_id)
        form.addRow("Account ID", self.account_id)
        form_buttons = QHBoxLayout()
        form_buttons.addStretch()
        form_buttons.addWidget(self.btn_lookup)
        form_buttons.addWidget(self.btn_save_settings)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["Tunnel", "Status", "Hostname", "Local service"]
        )
        self.table.setAccessibleName("Tunnels and routes")
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for col, width in enumerate((200, 90, 260)):
            self.table.setColumnWidth(col, width)

        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_add = QPushButton("Add route…")
        self.btn_add.clicked.connect(self.add_route)
        self.btn_edit = QPushButton("Edit route…")
        self.btn_edit.setToolTip(
            "Change the local service (port) of the selected route"
        )
        self.btn_edit.clicked.connect(self.edit_selected_route)
        self.btn_remove = QPushButton("Remove route")
        self.btn_remove.clicked.connect(self.remove_selected_route)
        self.btn_delete_tunnel = QPushButton("Delete tunnel…")
        self.btn_delete_tunnel.setToolTip(
            "Delete the selected tunnel (only tunnels created by raspi-manager, and only when no connector is running)"
        )
        self.btn_delete_tunnel.clicked.connect(self.delete_selected_tunnel)
        self.btn_dns = QPushButton("DNS records…")
        self.btn_dns.setToolTip("List every DNS record of the zone (read-only)")
        self.btn_dns.clicked.connect(self.show_dns)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        actions = QHBoxLayout()
        for w in (
            self.btn_add,
            self.btn_edit,
            self.btn_remove,
            self.btn_delete_tunnel,
            self.btn_dns,
            self.btn_refresh,
        ):
            actions.addWidget(w)
        actions.addStretch()
        actions.addWidget(close)

        self.note = QLabel("")
        self.note.setWordWrap(True)
        layout = QVBoxLayout(self)
        for item in (QLabel("<b>API token</b>"),):
            layout.addWidget(item)
        layout.addLayout(token_row)
        layout.addWidget(self.token_state)
        layout.addWidget(QLabel("<b>Domain</b>"))
        layout.addLayout(form)
        layout.addLayout(form_buttons)
        layout.addWidget(QLabel("<b>Tunnels and routes</b>"))
        layout.addWidget(self.table, 1)
        layout.addWidget(self.note)
        layout.addLayout(actions)

        s = store.load_settings()
        self.zone_name.setText(s.zone_name)
        self.zone_id.setText(s.zone_id)
        self.account_id.setText(s.account_id)
        self._update_token_state()
        self._rows: list[tuple[str, str]] = []  # (tunnel_id, hostname) per table row
        self._sticky = ""
        self._last_hostname = ""
        self._request: tuple[str, str, str, str] | None = None
        if self._ready():
            self.refresh()

    # --- helpers ---------------------------------------------------------------
    def _settings(self) -> store.CloudflareSettings:
        return store.CloudflareSettings(
            self.account_id.text().strip(),
            self.zone_id.text().strip(),
            self.zone_name.text().strip().lower(),
        )

    def _client(self) -> CloudflareClient | None:
        try:
            token = store.get_token()
        except store.SecretStoreError as exc:
            self._warn(str(exc))
            return None
        if not token:
            self._warn("Save an API token first.")
            return None
        return CloudflareClient(token)

    def _ready(self) -> bool:
        try:
            return bool(store.get_token()) and store.load_settings().complete
        except store.SecretStoreError:
            return False

    def _warn(self, message: str) -> None:
        self.note.setText(message)

    def _update_token_state(self) -> None:
        try:
            has = bool(store.get_token())
        except store.SecretStoreError as exc:
            self.token_state.setText(str(exc))
            return
        self.token_state.setText(
            "A token is stored in the Windows Credential Manager (never shown here, never written to a file)."
            if has
            else "No token stored yet."
        )
        self.btn_remove_token.setEnabled(has)

    # --- token and settings ---------------------------------------------------------
    def save_token(self) -> None:
        try:
            store.set_token(self.token_edit.text())
        except store.SecretStoreError as exc:
            self._warn(str(exc))
            return
        self.token_edit.clear()
        self._update_token_state()
        self.note.setText(
            "Token saved. Now fill in the domain, then “Look up from token”."
        )

    def remove_token(self) -> None:
        if (
            QMessageBox.question(self, APP_NAME, "Remove the stored Cloudflare token?")
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            store.delete_token()
        except store.SecretStoreError as exc:
            self._warn(str(exc))
            return
        self._update_token_state()
        self.note.setText("Token removed.")

    def save_settings(self) -> bool:
        try:
            store.save_settings(self._settings())
        except RouteError as exc:
            self._warn(str(exc))
            return False
        self.note.setText(
            "Saved to ~/.raspi/cloudflare.yaml (identifiers only, no secret)."
        )
        return True

    def lookup(self) -> None:
        client, name = self._client(), self.zone_name.text().strip().lower()
        if not client:
            return
        if not name:
            self._warn("Type your domain (zone name) first, for example example.com.")
            return
        self.note.setText("Looking up the zone…")
        submit(lambda: client.list_zones(name), self._on_zones, self._on_error)

    @Slot(object)
    def _on_zones(self, zones: list[dict]) -> None:
        if not zones:
            self._warn(
                "No zone with this name is visible to the token. Check the name and the token's zone access."
            )
            return
        zone = zones[0]
        self.zone_id.setText(zone.get("id", ""))
        account = (zone.get("account") or {}).get("id", "")
        if account:
            self.account_id.setText(account)
        self.note.setText(
            "Zone found."
            + (
                ""
                if account
                else " The account ID was not returned: copy it from the Cloudflare dashboard."
            )
        )
        if self.save_settings():
            self.refresh()

    def _on_error(self, message: str) -> None:
        self._warn(message)

    # --- tunnels and routes ----------------------------------------------------------------
    def refresh(self, keep_note: str = "") -> None:
        """Reload the table. `keep_note` (e.g. "Done: ...") stays visible after the reload."""
        client, settings = self._client(), self._settings()
        if not client or not settings.account_id:
            return
        self._sticky = keep_note
        if not keep_note:
            self.note.setText("Reading tunnels…")
        submit(
            lambda: routing.overview(client, settings),
            self._on_overview,
            self._on_error,
        )

    @Slot(object)
    def _on_overview(self, tunnels: list[routing.TunnelInfo]) -> None:
        self.table.setRowCount(0)
        self._rows = []
        for t in tunnels:
            for route in t.routes or [None]:
                row = self.table.rowCount()
                self.table.insertRow(row)
                cells = [
                    t.name,
                    t.status,
                    route.hostname if route else "—",
                    route.service if route else "—",
                ]
                for col, text in enumerate(cells):
                    self.table.setItem(row, col, QTableWidgetItem(text))
                self._rows.append((t.id, route.hostname if route else ""))
        summary = f"{len(tunnels)} tunnel(s). Status “inactive” or “down” means no connector is running on a Pi yet."
        self.note.setText(f"{self._sticky} {summary}" if self._sticky else summary)
        self._sticky = ""

    def show_dns(self) -> None:
        settings, client = self._settings(), self._client()
        if not client:
            return
        if not settings.complete:
            self._warn(
                "Fill in and save the zone and account first (use “Look up from token”)."
            )
            return
        DnsRecordsDialog(client, settings, self._last_hostname, self).exec()

    def edit_selected_route(self) -> None:
        """Change the local service of an existing route (same plan/confirm/apply flow as adding)."""
        rows = self.table.selectionModel().selectedRows()
        if not rows or not self._rows[rows[0].row()][1]:
            self._warn("Select a route first.")
            return
        row = rows[0].row()
        tunnel_name = self.table.item(row, 0).text()
        parsed = routing.parse_tunnel_name(tunnel_name, [h.name for h in self._hosts])
        if parsed is None:
            self._warn(
                "This tunnel was not created by raspi-manager for one of your Pis: edit its routes in the Cloudflare dashboard."
            )
            return
        host, project = parsed
        self.add_route(
            prefill=(
                host,
                self._rows[row][1],
                self.table.item(row, 3).text(),
                project or "",
            )
        )

    def add_route(self, prefill: tuple[str, str, str, str] | None = None) -> None:
        settings, client = self._settings(), self._client()
        if not client:
            return
        if not settings.complete:
            self._warn(
                "Fill in and save the zone and account first (use “Look up from token”)."
            )
            return
        dialog = AddRouteDialog(self._hosts, settings.zone_name, self, prefill)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.result_value:
            return
        host, hostname, service, project = dialog.result_value
        self._last_hostname = (
            hostname.strip().lower()
        )  # pre-fills the DNS list if this conflicts
        self._request = (host, hostname, service, project)
        self._plan(repoint=False)

    def _plan(self, repoint: bool) -> None:
        client, settings = self._client(), self._settings()
        if not client or self._request is None:
            return
        host, hostname, service, project = self._request
        self.note.setText("Checking what would change…")

        def job():
            try:
                return routing.plan_add_route(
                    client, settings, host, hostname, service, repoint, project
                )
            except routing.DnsConflictError as conflict:
                return conflict  # handed back to the GUI thread to ask what to do

        submit(job, self._on_plan, self._on_error)

    def _ask_repoint(self, conflict: routing.DnsConflictError) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(APP_NAME)
        box.setText("This hostname already has a DNS record.")
        box.setInformativeText(
            f"{conflict}\n\nYou can point it to the new tunnel instead. You will see the exact change "
            "(and the previous target) before anything is modified."
        )
        go = box.addButton("Repoint it…", QMessageBox.ButtonRole.AcceptRole)
        dns = box.addButton("Show DNS records", QMessageBox.ButtonRole.ActionRole)
        cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is go:
            self._plan(repoint=True)
        elif clicked is dns:
            self.note.setText("Nothing was changed.")
            self.show_dns()
        else:
            self.note.setText("Cancelled, nothing was changed.")

    @Slot(object)
    def _on_plan(self, plan: routing.RoutePlan | routing.DnsConflictError) -> None:
        if isinstance(plan, routing.DnsConflictError):
            if plan.repointable:
                self._ask_repoint(plan)
            else:
                self.note.setText(str(plan))
            return
        if plan.nothing_to_do:
            self.note.setText("Already set up: nothing to change.")
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(APP_NAME)
        box.setText("This will change your Cloudflare account:")
        box.setInformativeText("\n".join(f"• {s}" for s in plan.steps))
        go = box.addButton("Apply", QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        box.exec()
        if box.clickedButton() is not go:
            self.note.setText("Cancelled, nothing was changed.")
            return
        client, settings = self._client(), self._settings()
        if not client:
            return
        self._busy(True)
        submit(
            lambda: routing.apply_add_route(client, settings, plan),
            self._on_applied,
            self._on_apply_failed,
        )

    @Slot(object)
    def _on_applied(self, result: routing.ApplyResult) -> None:
        self._busy(False)
        suffix = (
            " The tunnel has no connector yet: install cloudflared on the Pi to bring it online."
            if result.created_tunnel
            else ""
        )
        message = "Done: " + "; ".join(result.messages) + "." + suffix
        self.note.setText(message)
        self.refresh(keep_note=message)

    def _on_apply_failed(self, message: str) -> None:
        self._busy(False)
        QMessageBox.critical(
            self,
            APP_NAME,
            f"Cloudflare change failed (some steps may have been applied):\n{message}",
        )
        self.refresh()

    def delete_selected_tunnel(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            self._warn("Select a row of the tunnel you want to delete.")
            return
        tunnel_id = self._rows[rows[0].row()][0]
        client, settings = self._client(), self._settings()
        if not client:
            return
        host_names = [h.name for h in self._hosts]
        self.note.setText("Checking the tunnel…")
        submit(
            lambda: routing.plan_delete_tunnel(client, settings, tunnel_id, host_names),
            self._on_delete_plan,
            self._on_error,
        )

    @Slot(object)
    def _on_delete_plan(self, plan: routing.DeletePlan) -> None:
        lines = [
            f"Tunnel '{plan.name}' (status {plan.status}) will be deleted. This cannot be undone."
        ]
        if plan.routes:
            lines.append(
                "Its routes disappear with it: "
                + ", ".join(r.hostname for r in plan.routes)
            )
        if plan.dns_hostnames:
            lines.append(
                "These DNS records point to it and will stop working (they are NOT deleted): "
                + ", ".join(plan.dns_hostnames)
            )
        lines.append("The cloudflared service on the Pi is not touched.")
        text, ok = QInputDialog.getText(
            self,
            APP_NAME,
            "\n".join([*lines, "", "Type the tunnel name to confirm:"]),
        )
        if not ok or text.strip() != plan.name:
            self.note.setText(
                "Cancelled, nothing was deleted."
                if not ok
                else "The name did not match: nothing was deleted."
            )
            return
        client, settings = self._client(), self._settings()
        if not client:
            return
        self._busy(True)
        submit(
            lambda: routing.apply_delete_tunnel(client, settings, plan),
            self._on_deleted,
            self._on_apply_failed,
        )

    @Slot(object)
    def _on_deleted(self, message: str) -> None:
        self._busy(False)
        self.note.setText(message)
        self.refresh(keep_note=message)

    def remove_selected_route(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows or not self._rows[rows[0].row()][1]:
            self._warn("Select a route first.")
            return
        tunnel_id, hostname = self._rows[rows[0].row()]
        if (
            QMessageBox.question(
                self,
                APP_NAME,
                f"Stop routing {hostname} through this tunnel?\nThe DNS record is not deleted.",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        client, settings = self._client(), self._settings()
        if not client:
            return
        self._busy(True)
        submit(
            lambda: routing.remove_route(client, settings, tunnel_id, hostname),
            self._on_removed,
            self._on_apply_failed,
        )

    @Slot(object)
    def _on_removed(self, message: str) -> None:
        self._busy(False)
        self.note.setText(message)
        self.refresh(keep_note=message)

    def _busy(self, busy: bool) -> None:
        for b in (
            self.btn_add,
            self.btn_edit,
            self.btn_remove,
            self.btn_delete_tunnel,
            self.btn_dns,
            self.btn_refresh,
            self.btn_lookup,
        ):
            b.setEnabled(not busy)


__all__ = ["AddRouteDialog", "CloudflareDialog", "CloudflareError", "Qt"]
