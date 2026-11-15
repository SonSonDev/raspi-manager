"""Form for a new app: name, GitHub repository, build and run commands, optional folder."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
)

from raspi.config.loader import ConfigError, parse_manifest, parse_requirement_line
from raspi.core.models import AppRef, Host


def app_from_fields(
    name: str, repo: str, build: str, run: str, path: str, requires: str = ""
) -> AppRef:
    """Validate the form like the loader does. Raises ConfigError.

    `requires`: one prerequisite per line, `<folder|file|mount> <absolute path>`.
    """
    entry: dict = {
        "name": name.strip(),
        "unit": f"{name.strip()}.service",
        "repo": repo,
    }
    for key, value in (("build", build), ("run", run), ("path", path)):
        if value.strip():
            entry[key] = value.strip()
    lines = [ln for ln in requires.splitlines() if ln.strip()]
    if lines:
        entry["requires"] = [
            {"kind": r.kind, "path": r.path}
            for r in (parse_requirement_line(ln) for ln in lines)
        ]
    return parse_manifest({"version": 1, "apps": [entry]})[0]


class InstallAppDialog(QDialog):
    """On accept, `result_value` is the AppRef to install."""

    def __init__(
        self,
        host: Host,
        parent=None,
        existing: AppRef | None = None,
        wizard: bool = False,
    ) -> None:
        super().__init__(parent)
        self.result_value: AppRef | None = None
        self._existing = existing
        self._taken = {a.name for a in host.apps} - (
            {existing.name} if existing else set()
        )
        self.setWindowTitle(
            f"{'Update' if existing else 'New' if wizard else 'Install'} an app - {host.name}"
        )
        self.resize(560, 400)

        intro = QLabel(
            "Tell it once what the app is; the next window then takes it through the "
            "deploy key, the clone and build, the environment, the unit and the start, "
            "one confirmed step at a time. The grey texts are examples, not values."
            if wizard
            else "Clones the repository on the Pi through its deploy key (create it first "
            "with “Deploy key…”), runs the build command, then adds the app to the "
            "Pi's inventory (or updates it). The grey texts are examples, not "
            "values. Nothing is changed before you confirm."
        )
        intro.setWordWrap(True)
        self.name_edit = self._line("App name", "e.g. notes-app")
        self.repo_edit = self._line("GitHub repository", "e.g. owner/name")
        self.build_edit = self._line(
            "Build command", "e.g. npm ci && npm run build (empty: no build)"
        )
        self.run_edit = self._line("Run command", "e.g. npm start (required)")
        self.path_edit = self._line("Folder on the Pi", "empty: ~/apps/<name>")
        self.requires_edit = QPlainTextEdit()
        self.requires_edit.setAccessibleName("Prerequisites")
        self.requires_edit.setPlaceholderText(
            "optional, one per line, e.g.\nmount /mnt/data\nfile /mnt/data/.marker"
        )
        self.requires_edit.setFixedHeight(64)
        if existing:
            self.name_edit.setText(existing.name)
            self.name_edit.setReadOnly(True)
            self.repo_edit.setText(existing.repo or "")
            self.build_edit.setText(existing.build or "")
            self.run_edit.setText(existing.run or "")
            self.path_edit.setText(existing.path or "")
            self.requires_edit.setPlainText(
                "\n".join(f"{r.kind} {r.path}" for r in existing.requires)
            )
        self.error = QLabel("")
        self.error.setObjectName("banner")
        self.error.setVisible(False)

        form = QFormLayout()
        form.addRow("App name", self.name_edit)
        form.addRow("Repository", self.repo_edit)
        form.addRow("Build command", self.build_edit)
        form.addRow("Run command", self.run_edit)
        form.addRow("Folder (optional)", self.path_edit)
        form.addRow("Prerequisites", self.requires_edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            "Start setup" if wizard else "Check…"
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addLayout(form)
        layout.addWidget(self.error)
        layout.addWidget(buttons)

    @staticmethod
    def _line(accessible: str, placeholder: str) -> QLineEdit:
        edit = QLineEdit()
        edit.setAccessibleName(accessible)
        edit.setPlaceholderText(placeholder)
        return edit

    def _accept(self) -> None:
        try:
            app = app_from_fields(
                self.name_edit.text(),
                self.repo_edit.text(),
                self.build_edit.text(),
                self.run_edit.text(),
                self.path_edit.text(),
                self.requires_edit.toPlainText(),
            )
            if not app.run:
                raise ConfigError(
                    "A run command is required (e.g. npm start): the app's service needs it"
                )
            if app.name in self._taken:
                raise ConfigError(f"{app.name} is already in the inventory")
        except ConfigError as exc:
            self.error.setText(str(exc))
            self.error.setVisible(True)
            return
        self.result_value = app
        self.accept()
