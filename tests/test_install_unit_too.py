"""“Install app…” offers to rewrite the unit when it changes what the unit says."""

import base64
import re
from dataclasses import replace

import pytest
from PySide6.QtWidgets import QApplication
from typer.testing import CliRunner

from raspi.core.models import AppRef, EnvFile, Host
from raspi.services.install import install_app, plan_install
from raspi.ssh.transport import RunResult

OLD = AppRef(
    "notes-app",
    "notes-app.service",
    repo="me/notes-app",
    build="npm ci",
    run="npm start",
    env_files=(EnvFile("default", "/home/pi/apps/notes-app/.env"),),
)
NEW = replace(OLD, run="node server.js", env_files=())  # the form knows no env files
HOST = Host("pi", "10.0.0.1", "pi", (OLD,))


class Pi:
    def __init__(self, unit="ours", fail_unit=False):
        self.unit, self.fail_unit = unit, fail_unit
        self.scripts: list[str] = []
        self.manifest = None
        self.unit_text = ""

    def run_script(self, host, script, timeout=20.0):
        self.scripts.append(script)
        if "daemon-reload" in script:
            if self.fail_unit:
                return RunResult(False, "", "sudo: a password is required")
            encoded = re.search(r"printf %s ([A-Za-z0-9+/=]+) \|", script)[1]
            self.unit_text = base64.b64decode(encoded).decode()
            return RunResult(True, "installed: notes-app.service\n")
        if 'echo "sudo=' in script:
            return RunResult(
                True,
                f"home=/home/pi\nsudo=yes\nunit={self.unit}\nenvfile0=yes\n"
                "nodebin=/home/pi/.nvm/bin\n",
            )
        if "state=" in script:
            return RunResult(True, "key=yes\nstate=repo\ndisk_mb=9000\n")
        if "git rev-parse" in script:
            return RunResult(True, "rev=abc1234\n")
        if "base64 -d" in script:
            encoded = re.search(r"printf %s '?([A-Za-z0-9+/=]+)'? \|", script)[1]
            self.manifest = base64.b64decode(encoded).decode()
            return RunResult(True, "written\n")
        if self.manifest is None:
            return RunResult(True, "@@MISSING\n")
        return RunResult(True, "@@EXISTS\n" + self.manifest)

    def unit_scripts(self):
        return [s for s in self.scripts if "daemon-reload" in s]


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_a_changed_run_command_offers_the_unit_with_the_declared_env_files():
    plan = plan_install(HOST, Pi(), NEW)
    assert plan.unit is not None and plan.unit.existing
    assert 'exec node server.js"' in plan.unit.text
    # env files are the inventory's, not the form's empty ones
    assert "EnvironmentFile=/home/pi/apps/notes-app/.env" in plan.unit.text


@pytest.mark.parametrize(
    ("known", "wanted", "unit"),
    [
        (None, NEW, "ours"),  # a new app: nothing to replace
        (OLD, replace(OLD, build="make"), "ours"),  # does not reach the unit
        (OLD, NEW, "missing"),  # “Install unit…” creates it
        (OLD, NEW, "foreign"),  # never touched
        (OLD, replace(OLD, run=None), "ours"),  # nothing to render
    ],
)
def test_no_offer_when_the_unit_is_not_ours_to_replace(known, wanted, unit):
    host = replace(HOST, apps=(known,) if known else ())
    assert plan_install(host, Pi(unit), wanted).unit is None


def test_ticked_rewrites_the_unit_after_the_inventory_and_never_restarts():
    pi = Pi()
    plan = plan_install(HOST, pi, NEW)
    pi.scripts.clear()
    result = install_app(HOST, pi, plan, with_unit=True)
    assert result.ok and "unit was rewritten" in result.message
    assert "node server.js" in pi.manifest and 'exec node server.js"' in pi.unit_text
    assert not any("restart" in s or "systemctl start" in s for s in pi.scripts)
    last_inventory = max(
        i
        for i, s in enumerate(pi.scripts)
        if "base64 -d" in s and "daemon-reload" not in s
    )
    assert last_inventory < pi.scripts.index(pi.unit_scripts()[0])


def test_unticked_leaves_the_unit_alone():
    pi = Pi()
    result = install_app(HOST, pi, plan_install(HOST, pi, NEW))
    assert result.ok and not pi.unit_scripts()


def test_a_failed_unit_write_is_reported_after_the_inventory_was_saved():
    pi = Pi(fail_unit=True)
    result = install_app(HOST, pi, plan_install(HOST, pi, NEW), with_unit=True)
    assert not result.ok and "unit was not updated" in result.message
    assert "updated in the inventory" in result.message and pi.manifest


def _gui(qapp, monkeypatch, tick, confirmed=True):
    from raspi.gui import install_actions
    from raspi.gui.main_window import MainWindow
    from raspi.gui.theme import LIGHT

    shown = []
    monkeypatch.setattr(install_actions, "submit", lambda fn, ok, bad=None: ok(fn()))
    for name in ("information", "critical"):
        monkeypatch.setattr(
            install_actions.QMessageBox, name, lambda *a: shown.append(a[2])
        )
    asked = []

    def option(*args):
        asked.append(args)
        return confirmed, tick

    monkeypatch.setattr(install_actions, "confirm_with_option", option)
    pi = Pi()
    window = MainWindow([HOST], pi, LIGHT, auto_refresh=False)
    window._on_install_planned((HOST, plan_install(HOST, pi, NEW)))
    window.close()
    return pi, shown, asked


def test_gui_offers_the_tick_box_and_follows_it(qapp, monkeypatch):
    pi, shown, asked = _gui(qapp, monkeypatch, tick=True)
    assert "not restarted" in asked[0][-1] and "notes-app.service" in asked[0][-1]
    assert "Unit file (if ticked)" in asked[0][4]
    assert pi.unit_scripts() and "unit was rewritten" in shown[0]
    pi, shown, _ = _gui(qapp, monkeypatch, tick=False)
    assert not pi.unit_scripts() and "unit was rewritten" not in shown[0]


def test_gui_cancel_changes_nothing(qapp, monkeypatch):
    pi, shown, _ = _gui(qapp, monkeypatch, tick=True, confirmed=False)
    assert not pi.unit_scripts() and not pi.manifest and not shown


def test_cli_dry_run_shows_the_unit_and_keep_unit_hides_it(monkeypatch):
    from raspi.cli import main as cli_main

    cli = cli_main.app
    pi = Pi()
    monkeypatch.setattr(cli_main, "_live_host", lambda name: (HOST, HOST))
    monkeypatch.setattr("raspi.cli.provision.SshTransport", lambda: pi)
    args = ["host", "install-app", "pi", "notes-app", "--repo", "me/notes-app"]
    args += ["--build", "npm ci", "--run", "node server.js", "--dry-run"]
    shown = CliRunner().invoke(cli, args)
    assert shown.exit_code == 0 and "Unit file:" in shown.output
    assert "not restarted" in shown.output
    hidden = CliRunner().invoke(cli, [*args, "--keep-unit"])
    assert "Unit file:" not in hidden.output and not pi.unit_scripts()
