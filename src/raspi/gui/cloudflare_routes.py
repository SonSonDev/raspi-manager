"""Route and tunnel actions of the Cloudflare window (add, edit, remove, delete, DNS list)."""

from __future__ import annotations

from PySide6.QtCore import Slot
from PySide6.QtWidgets import (
    QDialog,
    QInputDialog,
    QMessageBox,
)

from raspi import APP_NAME
from raspi.gui.add_route_dialog import AddRouteDialog
from raspi.gui.dns_dialog import DnsRecordsDialog
from raspi.gui.workers import submit
from raspi.services import routing, routing_delete


class CloudflareRoutesMixin:
    """Mixed into CloudflareDialog: relies on its `_client`, `_settings`, `_ready`, `_warn`, `_busy`,
    `refresh`, `table`, `note` and `_hosts`."""

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
            lambda: routing_delete.plan_delete_tunnel(
                client, settings, tunnel_id, host_names
            ),
            self._on_delete_plan,
            self._on_error,
        )

    @Slot(object)
    def _on_delete_plan(self, plan: routing_delete.DeletePlan) -> None:
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
            lambda: routing_delete.apply_delete_tunnel(client, settings, plan),
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
