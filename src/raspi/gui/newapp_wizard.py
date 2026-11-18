"""“New app” wizard: key, clone and build, environment, unit, start, one confirmed step at a time.

Where the app stands is read from the Pi before and after every step (never remembered here),
so a failed or abandoned step leaves nothing half-recorded and the wizard reopens at that step.
Only the current step can be run. Every change shows its exact script first, like the separate
menu items it chains.
"""

from __future__ import annotations

from PySide6.QtCore import Slot
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from raspi import APP_NAME
from raspi.core.deploykey import key_steps
from raspi.core.models import AppRef, Host
from raspi.core.processes import all_processes
from raspi.core.requires import missing_lines
from raspi.core.wizard import (
    ENV,
    GLYPH,
    INSTALL,
    KEY,
    START,
    UNIT,
    Step,
    current_step,
    wizard_steps,
)
from raspi.gui.add_env_dialog import AddEnvFileDialog
from raspi.gui.confirm import confirm_change
from raspi.gui.declare import run_declaration
from raspi.gui.env_dialog import EnvDialog
from raspi.gui.workers import submit
from raspi.services.actions import run_processes
from raspi.services.declare import declare_env_file
from raspi.services.deploykey import ensure_key, verify_repo
from raspi.services.install import install_app, plan_install
from raspi.services.inventory import read_inventory
from raspi.services.unit import install_unit, plan_unit
from raspi.services.wizard import read_status
from raspi.ssh.transport import Transport

RUN_TEXT = {
    KEY: "Create and check the key…",
    INSTALL: "Clone and build…",
    ENV: "Open the environment editor…",
    UNIT: "Create the unit…",
    START: "Start the app…",
}


HINT = {
    KEY: "Next: create the deploy key on the Pi and check that GitHub accepts it.",
    INSTALL: "Next: clone the repository and build it.",
    ENV: (
        "Next: declare the environment file and fill in its variables "
        "(or say the app needs none)."
    ),
    UNIT: "Next: create the systemd unit; it is enabled but not started.",
    START: "Next: start the app.",
}
READING = "Reading the Pi…"


class NewAppWizard(QDialog):
    def __init__(
        self, host: Host, app: AppRef, transport: Transport, parent=None
    ) -> None:
        super().__init__(parent)
        self.host, self.app, self._transport = host, app, transport
        self._skipped: set[str] = set()
        self._verified = False  # GitHub accepted the key during this session
        self._steps: list[Step] = []
        self._status = None  # last status read from the Pi
        self.setWindowTitle(f"New app - {app.name} on {host.name}")
        self.resize(620, 300)

        header = QLabel(f"Setting up <b>{app.name}</b> on <b>{host.name}</b>")
        header.setAccessibleName("Setup of the app")
        self._glyphs: dict[str, QLabel] = {}
        self._titles: dict[str, QLabel] = {}
        self._details: dict[str, QLabel] = {}
        rows = QVBoxLayout()
        bold = QFont()
        bold.setBold(True)
        for key in (KEY, INSTALL, ENV, UNIT, START):
            line = QHBoxLayout()
            glyph, title, detail = QLabel("○"), QLabel(""), QLabel("")
            glyph.setFixedWidth(20)
            title.setFixedWidth(130)
            title.setFont(bold)
            line.addWidget(glyph)
            line.addWidget(title)
            line.addWidget(detail, 1)
            rows.addLayout(line)
            self._glyphs[key], self._titles[key], self._details[key] = (
                glyph,
                title,
                detail,
            )
        self.note = QLabel(READING)
        self.note.setWordWrap(True)
        self.note.setAccessibleName("Last result")

        self.run_btn = QPushButton("Run")
        self.run_btn.setDefault(True)
        self.run_btn.clicked.connect(self.run_current)
        self.skip_btn = QPushButton("The app needs no environment file")
        self.skip_btn.clicked.connect(self._skip_env)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        buttons.addWidget(self.skip_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.run_btn)
        buttons.addWidget(close)

        layout = QVBoxLayout(self)
        layout.addWidget(header)
        layout.addLayout(rows)
        layout.addWidget(self.note)
        layout.addStretch(1)
        layout.addLayout(buttons)
        self.skip_btn.hide()
        self.refresh()

    # --- reading the Pi -----------------------------------------------------------
    def refresh(self) -> None:
        host, app, transport = self.host, self.app, self._transport
        self._busy(True)

        def job():
            inv = read_inventory(host, transport)
            fresh_host = inv.host
            fresh_app = next((a for a in fresh_host.apps if a.name == app.name), app)
            return fresh_host, fresh_app, read_status(fresh_host, transport, fresh_app)

        submit(job, self._on_status, self._on_status_failed)

    @Slot(object)
    def _on_status(self, payload) -> None:
        self.host, self.app, status = payload
        self._status = status
        self._steps = wizard_steps(
            self.app, status, frozenset(self._skipped), self._verified
        )
        self._render()
        self._busy(False)

    def _on_status_failed(self, message: str) -> None:
        self._busy(False)
        self.run_btn.setEnabled(False)
        self.note.setText(f"✕ {message}")

    def _render(self) -> None:
        for step in self._steps:
            self._glyphs[step.key].setText(GLYPH[step.state])
            self._titles[step.key].setText(step.label)
            self._details[step.key].setText(step.detail)
        current = current_step(self._steps)
        self.skip_btn.setVisible(bool(current and current.key == ENV))
        if current is None:
            self.run_btn.setText("Done")
            self.run_btn.setEnabled(False)
            self.note.setText(
                f"✓ {self.app.name} is set up and running. To put it online, use "
                "Cloudflare → Set up route…"
            )
        else:
            self.run_btn.setText(RUN_TEXT[current.key])
            self.run_btn.setEnabled(True)
            if (
                self.note.text() == READING
            ):  # keep the result of the last step otherwise
                self.note.setText(HINT[current.key])

    def _busy(self, on: bool, text: str = "") -> None:
        self.run_btn.setEnabled(not on and current_step(self._steps) is not None)
        self.skip_btn.setEnabled(not on)
        if text:
            self.note.setText(text)

    def _failed(self, message: str) -> None:
        self.note.setText(f"✕ {message}")
        QMessageBox.critical(self, APP_NAME, message)
        self.refresh()  # the Pi may have changed even though the step failed

    # --- running the current step ----------------------------------------------------
    def run_current(self) -> None:
        step = current_step(self._steps)
        if step is None:
            return
        {
            KEY: self._do_key,
            INSTALL: self._do_install,
            ENV: self._do_env,
            UNIT: self._do_unit,
            START: self._do_start,
        }[step.key]()

    def _skip_env(self) -> None:
        self._skipped.add(ENV)
        self.refresh()

    # key
    def _do_key(self) -> None:
        host, name, transport = self.host, self.app.name, self._transport
        self._busy(True, "Preparing the key…")
        submit(
            lambda: ensure_key(host, transport, name, dry_run=True),
            self._key_planned,
            self._failed,
        )

    @Slot(object)
    def _key_planned(self, plan) -> None:
        if not plan.ok:
            self._failed(plan.message)
            return
        host, app, transport = self.host, self.app, self._transport
        if not confirm_change(
            self,
            f"Create the deploy key of {app.name} on {host.name}?",
            key_steps(app.name, host.name),
            ["An existing key and settings are kept as they are"],
            f"Script run on {host.name}:\n\n{plan.script}",
            "Create and check",
        ):
            self._busy(False, "Cancelled, nothing was changed.")
            return
        self._busy(True, "Creating the key and asking GitHub…")

        def job():
            made = ensure_key(host, transport, app.name)
            checked = (
                verify_repo(host, transport, app.name, app.repo or "")
                if made.ok
                else None
            )
            return made, checked

        submit(job, self._key_done, self._failed)

    @Slot(object)
    def _key_done(self, payload) -> None:
        made, checked = payload
        if not made.ok:
            self._failed(made.message)
            return
        if checked is not None and checked.ok:
            self._verified = True
            self.note.setText("✓ GitHub accepts the key.")
        else:
            self._show_public_key(made.public_key, checked.message if checked else "")
        self.refresh()

    def _show_public_key(self, key: str, problem: str) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle(APP_NAME)
        box.setText(f"Add this key to {self.app.repo} on GitHub")
        box.setInformativeText(
            "Repository → Settings → Deploy keys → Add deploy key (leave “Allow write "
            "access” unticked), then run this step again to check it.\n\n"
            f"{key}\n\n{problem}"
        )
        copy = box.addButton("Copy key", QMessageBox.ButtonRole.ActionRole)
        box.addButton("Close", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is copy:
            QGuiApplication.clipboard().setText(key)
        self.note.setText(
            "The key is created. Add it on GitHub, then run this step again."
        )

    # clone and build
    def _do_install(self) -> None:
        host, app, transport = self.host, self.app, self._transport
        self._busy(True, "Checking what installing would do…")
        submit(
            lambda: plan_install(host, transport, app),
            self._install_planned,
            self._failed,
        )

    @Slot(object)
    def _install_planned(self, plan) -> None:
        host, transport = self.host, self._transport
        if not confirm_change(
            self,
            f"Install {plan.app.name} on {host.name}?",
            plan.steps,
            [],
            f"Script run on {host.name}:\n\n{plan.script}",
            "Install",
        ):
            self._busy(False, "Cancelled, nothing was changed.")
            return
        self._busy(True, "Cloning and building… (this can take several minutes)")
        submit(lambda: install_app(host, transport, plan), self._changed, self._failed)

    # environment
    def _do_env(self) -> None:
        host, app, transport = self.host, self.app, self._transport
        if not app.env_files:
            dialog = AddEnvFileDialog(host, app, transport, set(), set(), self, True)
            if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.result_value:
                return
            label, path = dialog.result_value
            result = run_declaration(
                lambda: declare_env_file(host, app.name, label, path, transport, True)
            )
            if not result.ok or result.app is None:
                QMessageBox.warning(self, APP_NAME, result.message)
                return
            app = result.app
        EnvDialog(host, app, transport, self, on_pi=True).exec()
        self.refresh()

    # unit
    def _do_unit(self) -> None:
        host, app, transport = self.host, self.app, self._transport
        self._busy(True, "Checking the unit…")
        submit(
            lambda: plan_unit(host, transport, app), self._unit_planned, self._failed
        )

    @Slot(object)
    def _unit_planned(self, plan) -> None:
        host, transport = self.host, self._transport
        if not confirm_change(
            self,
            f"Create the unit of {plan.app.name} on {host.name}?",
            plan.steps,
            plan.warnings,
            f"Unit file:\n\n{plan.text}",
            "Create",
        ):
            self._busy(False, "Cancelled, nothing was changed.")
            return
        self._busy(True, "Creating the unit…")
        submit(lambda: install_unit(host, transport, plan), self._changed, self._failed)

    # start
    def _do_start(self) -> None:
        host, app, transport = self.host, self.app, self._transport
        if not confirm_change(
            self,
            f"Start {app.name} on {host.name}?",
            [f"Run: sudo systemctl start {p.unit}" for p in all_processes(app)],
            missing_lines(app, self._status),
            "The unit was created in the previous step.",
            "Start",
        ):
            self.note.setText("Cancelled, nothing was changed.")
            return
        self._busy(True, "Starting…")
        submit(
            lambda: run_processes(host, app, "start", transport),
            self._changed,
            self._failed,
        )

    @Slot(object)
    def _changed(self, result) -> None:
        """Result of any step that changes the Pi: show it, then read the Pi again."""
        if not result.ok:
            self._failed(result.message)
            return
        self.note.setText(f"✓ {result.message}")
        self.refresh()
