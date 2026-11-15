"""“Set up route…”: put the selected app on the internet (Cloudflare + connector on its Pi)."""

from __future__ import annotations

from PySide6.QtCore import Slot
from PySide6.QtWidgets import QDialog, QMessageBox

from raspi import APP_NAME
from raspi.cloudflare.client import CloudflareClient
from raspi.config import cloudflare_settings as store
from raspi.core.models import AppRef, Host
from raspi.gui.route_dialog import RouteSetupDialog
from raspi.gui.workers import submit
from raspi.services import app_route, routing


class RouteMixin:
    """Mixed into MainWindow: relies on its `_transport`, `selected`, `model`,
    `status_label` and `_refresh_host`."""

    _route_request: tuple[Host, AppRef, str, int] | None = None

    def can_route(self) -> bool:
        row = self.selected()
        status = self.model.host_status(row.host.name) if row and row.app else None
        return bool(status and status.reachable)

    def setup_route(self) -> None:
        row = self.selected()
        if not row or not row.app:
            return
        try:
            token, settings = store.get_token(), store.load_settings()
        except store.SecretStoreError as exc:
            QMessageBox.warning(self, APP_NAME, str(exc))
            return
        if not token or not settings.complete:
            QMessageBox.information(
                self,
                APP_NAME,
                "Cloudflare is not set up yet. Open “Cloudflare…” (Ctrl+Shift+C), save "
                "the API token and the zone, then come back.",
            )
            return
        dialog = RouteSetupDialog(
            row.host, row.app, settings.zone_name, self._transport, self
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.result_value:
            return
        hostname, port = dialog.result_value
        self._route_request = (row.host, row.app, hostname, port)
        self._plan_route(CloudflareClient(token), settings, repoint=False)

    def _plan_route(self, client, settings, repoint: bool) -> None:
        if self._route_request is None:
            return
        host, app, hostname, port = self._route_request
        self.status_label.setText(f"Checking what a route for {app.name} would change…")

        def job():
            try:
                plan = app_route.plan_app_route(
                    client,
                    settings,
                    host,
                    app,
                    hostname,
                    port,
                    self._transport,
                    repoint,
                )
            except routing.DnsConflictError as conflict:
                return client, settings, conflict  # asked about on the GUI thread
            return client, settings, plan

        submit(job, self._on_route_planned, self._route_failed)

    @Slot(object)
    def _on_route_planned(self, payload) -> None:
        client, settings, plan = payload
        if isinstance(plan, routing.DnsConflictError):
            self._ask_repoint(client, settings, plan)
            return
        if plan.nothing_to_do:
            self.status_label.setText("Already set up: nothing to change.")
            QMessageBox.information(
                self, APP_NAME, "Already set up: nothing to change."
            )
            return
        if not self._confirm_route(plan):
            self.status_label.setText("Cancelled, nothing was changed.")
            return
        self.status_label.setText(
            f"Setting up {plan.app.name}… (this can take a minute)"
        )
        submit(
            lambda: (
                plan,
                app_route.apply_app_route(client, settings, plan, self._transport),
            ),
            self._on_route_applied,
            self._route_failed,
        )

    def _ask_repoint(self, client, settings, conflict) -> None:
        if not conflict.repointable:
            self.status_label.setText("Cannot set up the route.")
            QMessageBox.warning(self, APP_NAME, str(conflict))
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(APP_NAME)
        box.setText("This hostname already has a DNS record.")
        box.setInformativeText(
            f"{conflict}\n\nYou can point it at the new tunnel instead; the exact "
            "change is shown before anything is modified."
        )
        go = box.addButton("Repoint it…", QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        box.exec()
        if box.clickedButton() is go:
            self._plan_route(client, settings, repoint=True)
        else:
            self.status_label.setText("Cancelled, nothing was changed.")

    def _confirm_route(self, plan: app_route.AppRoutePlan) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(APP_NAME)
        host = plan.cloudflare.route.hostname
        box.setText(f"Put {plan.app.name} online at https://{host}?")
        box.setInformativeText(
            "This changes your Cloudflare account and the Pi:\n"
            + "\n".join(f"• {s}" for s in plan.steps)
            + "\n\nThe tunnel token is fetched at the last moment and sent to the Pi "
            "only; it is never shown or saved on this PC."
        )
        if plan.install:
            box.setDetailedText(
                f"Script run on {plan.host.name} (token hidden):\n\n{plan.preview_script()}"
            )
        confirm = box.addButton("Apply", QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        box.exec()
        return box.clickedButton() is confirm

    @Slot(object)
    def _on_route_applied(self, payload) -> None:
        plan, result = payload
        text = "\n".join(result.messages)
        if result.ok:
            self.status_label.setText(f"✓ {plan.app.name} is set up")
            QMessageBox.information(self, APP_NAME, text)
        else:
            self.status_label.setText(f"✕ Route for {plan.app.name} failed")
            QMessageBox.critical(self, APP_NAME, text)
        self._refresh_host(plan.host.name)

    def _route_failed(self, message: str) -> None:
        self.status_label.setText("Route set-up failed")
        QMessageBox.critical(self, APP_NAME, message)
