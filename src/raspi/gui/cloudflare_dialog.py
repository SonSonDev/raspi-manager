"""Cloudflare window: token (Windows Credential Manager), account/zone, tunnels and routes."""

from __future__ import annotations

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
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
from raspi.gui.add_route_dialog import AddRouteDialog
from raspi.gui.cloudflare_routes import CloudflareRoutesMixin
from raspi.gui.workers import submit
from raspi.services import routing


class CloudflareDialog(CloudflareRoutesMixin, QDialog):
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
