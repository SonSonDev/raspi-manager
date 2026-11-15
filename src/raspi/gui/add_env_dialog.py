"""Declare an environment file for an app: detect `.env*` candidates (read-only) or type a path."""

from __future__ import annotations

from PySide6.QtCore import Slot
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from raspi.config.loader import ConfigError, check_env_path, check_label
from raspi.core.install import work_dir
from raspi.core.models import AppRef, Host
from raspi.core.naming import suggest_label
from raspi.core.remote import EnvScan
from raspi.gui.workers import submit
from raspi.services.env import scan_env
from raspi.ssh.transport import Transport


class AddEnvFileDialog(QDialog):
    """On accept, `result_value` is (label, path)."""

    def __init__(
        self,
        host: Host,
        app: AppRef,
        transport: Transport,
        taken_labels: set[str],
        taken_paths: set[str],
        parent=None,
        on_pi: bool = False,
    ) -> None:
        super().__init__(parent)
        self._app, self._taken_labels, self._taken_paths = (
            app,
            taken_labels,
            taken_paths,
        )
        self.result_value: tuple[str, str] | None = None
        # an app without a declared path lives in ~/apps/<name> once it is installed
        self._folder = work_dir(app) if (app.path or app.repo) else None
        self._real_folder: str | None = None  # absolute folder reported by the Pi
        self._to_create: dict[str, str] = {}  # path of a file to create -> its template
        self.setWindowTitle(f"Declare an environment file - {app.name}")
        self.resize(640, 220)

        intro = QLabel(
            f"Which file does <b>{app.name}</b> read its variables from? "
            + (
                "The declaration is saved in ~/.raspi/apps.yaml on the Pi (the "
                "previous copy is kept as apps.yaml.bak); the .env file itself is "
                "not touched."
                if on_pi
                else "Nothing is created or changed on the Pi."
            )
        )
        intro.setWordWrap(True)
        self.path_box = QComboBox()
        self.path_box.setEditable(True)
        self.path_box.setAccessibleName("Path of the environment file on the Pi")
        self.path_box.lineEdit().setPlaceholderText("/home/user/app/.env")
        self.path_box.currentTextChanged.connect(self._suggest)
        self.label_edit = QLineEdit()
        self.label_edit.setAccessibleName("Label")
        self.hint = QLabel(
            "Looking for .env files and templates…"
            if app.path or app.repo
            else "Type the full path of the file on the Pi."
        )
        self.hint.setWordWrap(True)
        self.error = QLabel("")
        self.error.setObjectName("banner")
        self.error.setVisible(False)

        form = QFormLayout()
        form.addRow("File on the Pi", self.path_box)
        form.addRow("Label", self.label_edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Declare")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        for item in (intro, self.hint):
            layout.addWidget(item)
        layout.addLayout(form)
        layout.addWidget(self.error)
        layout.addWidget(buttons)

        if self._folder:
            folder = self._folder
            submit(
                lambda: scan_env(host, folder, transport),
                self._on_detected,
                lambda msg: self.hint.setText(
                    f"Could not look on the Pi: {msg}. You can still type a path."
                ),
            )

    @Slot(object)
    def _on_detected(self, scan: EnvScan | None) -> None:
        if scan is None:
            self.hint.setText(
                f"The folder {self._app.path or self._folder} does not exist on the Pi "
                "(is the app installed?). You can still type a path."
            )
            return
        self._real_folder = scan.folder
        existing = [p for p in scan.files if p not in self._taken_paths]
        # a template (.env.example) with no real file beside it: offer to create that file
        self._to_create = {}
        for template in scan.templates:
            target = template.rsplit(".", 1)[0]
            if target not in scan.files and target not in self._taken_paths:
                self._to_create[target] = template.rsplit("/", 1)[-1]
        choices = existing + [p for p in self._to_create if p not in existing]
        self.path_box.blockSignals(True)
        self.path_box.clear()
        self.path_box.addItems(choices)
        self.path_box.blockSignals(False)
        if choices:
            self.hint.setText(
                f"Found {len(existing)} file(s) and {len(self._to_create)} template(s) in "
                f"{scan.folder}. Pick one or type another path."
            )
            self._suggest(choices[0])
        else:
            self.hint.setText(
                f"No undeclared .env file or template found in {scan.folder}. "
                "Type a path if the app reads another one."
            )

    @Slot(str)
    def _suggest(self, path: str) -> None:
        if path in self._to_create:
            self.hint.setText(
                f"{path} does not exist yet: it is created when you push, and the "
                f"editor starts from {self._to_create[path]}."
            )
        if not self.label_edit.isModified() or not self.label_edit.text():
            self.label_edit.setText(
                suggest_label(
                    self._real_folder or self._app.path, path, self._taken_labels
                )
            )
            self.label_edit.setModified(False)

    def _accept(self) -> None:
        path = self.path_box.currentText().strip()
        label = self.label_edit.text().strip()
        try:
            path, label = check_env_path(path), check_label(label)
            if label in self._taken_labels:
                raise ConfigError(f"This app already has a file labelled '{label}'")
            if path in self._taken_paths:
                raise ConfigError("This path is already declared for this app")
        except ConfigError as exc:
            self.error.setText(str(exc))
            self.error.setVisible(True)
            return
        self.result_value = (label, path)
        self.accept()
