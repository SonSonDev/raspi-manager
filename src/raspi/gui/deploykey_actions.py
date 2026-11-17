"""“Deploy key…”: create a read-only GitHub deploy key on the selected Pi and check it works."""

from __future__ import annotations

from PySide6.QtCore import Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QInputDialog, QLineEdit, QMessageBox

from raspi import APP_NAME
from raspi.core.deploykey import DeployKeyError, check_name, check_repo, key_steps
from raspi.core.models import Host
from raspi.gui.deploykeys_dialog import DeployKeysDialog
from raspi.gui.workers import submit
from raspi.services.deploykey import KeyResult, ensure_key, verify_repo


class DeployKeyMixin:
    """Mixed into MainWindow: relies on its `_hosts`, `_transport`, `selected`,
    `selected_host` and `status_label`."""

    def can_deploy_key(self) -> bool:
        host = self.selected_host()
        status = self.model.host_status(host.name) if host else None
        return bool(status and status.reachable)

    def manage_deploy_keys(self) -> None:
        selected = self.selected_host()
        host = self._hosts.get(selected.name) if selected else None
        if selected is None or host is None:
            return
        DeployKeysDialog(
            host,
            selected.apps,
            self._transport,
            lambda: self._refresh_host(host.name),
            self,
        ).exec()

    def deploy_key(self) -> None:
        selected = self.selected_host()
        host = self._hosts.get(selected.name) if selected else None
        if host is None:
            return
        row = self.selected()
        name, ok = QInputDialog.getText(
            self,
            APP_NAME,
            "App name (the key and its ssh alias are named after it):",
            QLineEdit.EchoMode.Normal,
            row.app.name if row and row.app else "",
        )
        if not ok:
            return
        repo, ok = QInputDialog.getText(
            self,
            APP_NAME,
            "GitHub repository, owner/name (leave empty to only create the key):",
        )
        if not ok:
            return
        try:
            name = check_name(name)
            repo = check_repo(repo) if repo.strip() else ""
        except DeployKeyError as exc:
            QMessageBox.warning(self, APP_NAME, str(exc))
            return
        self.status_label.setText("Preparing the deploy key…")
        submit(
            lambda: (host, name, repo, ensure_key(host, self._transport, name, True)),
            self._on_key_planned,
            self._key_failed,
        )

    @Slot(object)
    def _on_key_planned(self, payload) -> None:
        host, name, repo, plan = payload
        if not plan.ok:
            self._key_failed(plan.message)
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(APP_NAME)
        box.setText(f"Create the deploy key of {name} on {host.name}?")
        box.setInformativeText(
            "This changes the Pi (only files in ~/.ssh):\n"
            + "\n".join(f"• {s}" for s in key_steps(name, host.name))
            + "\n\nAn existing key and settings are kept as they are."
        )
        box.setDetailedText(f"Script run on {host.name}:\n\n{plan.script}")
        confirm = box.addButton("Create", QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        box.exec()
        if box.clickedButton() is not confirm:
            self.status_label.setText("Cancelled, nothing was changed.")
            return
        self.status_label.setText(f"Creating the deploy key on {host.name}…")

        def job():
            made = ensure_key(host, self._transport, name)
            checked = (
                verify_repo(host, self._transport, name, repo)
                if made.ok and repo
                else None
            )
            return host, name, repo, made, checked

        submit(job, self._on_key_made, self._key_failed)

    @Slot(object)
    def _on_key_made(self, payload) -> None:
        host, name, repo, made, checked = payload
        if not made.ok:
            self._key_failed(made.message)
            return
        self._show_key(host, name, repo, made, checked)

    def _show_key(
        self,
        host: Host,
        name: str,
        repo: str,
        made: KeyResult,
        checked: KeyResult | None,
    ) -> None:
        good = checked is None or checked.ok
        self.status_label.setText(
            f"✓ Deploy key ready on {host.name}" if good else "✕ GitHub refused the key"
        )
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information if good else QMessageBox.Icon.Warning)
        box.setWindowTitle(APP_NAME)
        box.setText(f"Deploy key of {name} on {host.name}")
        intro = (
            "Add this public key on GitHub: repository, Settings, Deploy keys, "
            "Add deploy key (leave “Allow write access” unticked):"
        )
        lines = [intro, "", made.public_key]
        if checked is not None:
            lines += ["", ("✓ " if checked.ok else "✕ ") + checked.message]
        elif not repo:
            lines += ["", "Run “Deploy key…” again with the repository to check it."]
        box.setInformativeText("\n".join(lines))
        copy = box.addButton("Copy key", QMessageBox.ButtonRole.ActionRole)
        again = (
            box.addButton("Check again", QMessageBox.ButtonRole.ActionRole)
            if repo
            else None
        )
        box.addButton("Close", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is copy:
            QGuiApplication.clipboard().setText(made.public_key)
            self._show_key(host, name, repo, made, checked)
        elif again is not None and clicked is again:
            self.status_label.setText("Checking the repository…")
            submit(
                lambda: (
                    host,
                    name,
                    repo,
                    made,
                    verify_repo(host, self._transport, name, repo),
                ),
                self._on_key_made,
                self._key_failed,
            )

    def _key_failed(self, message: str) -> None:
        self.status_label.setText("Deploy key failed")
        QMessageBox.critical(self, APP_NAME, message)
