import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
import yaml
from PySide6.QtWidgets import QApplication
from typer.testing import CliRunner

from raspi.config.loader import (
    ConfigError,
    parse_manifest,
    parse_requirement_line,
)
from raspi.core.manifest import render_manifest
from raspi.core.models import AppRef, AppStatus, Host, Requirement
from raspi.core.remote import build_status_script, parse_status
from raspi.core.requires import describe, missing_lines, missing_requirements
from raspi.core.stage import next_step
from raspi.core.wizard import wizard_steps
from raspi.gui.install_dialog import InstallAppDialog, app_from_fields

MOUNT = Requirement("mount", "/mnt/data")
MARKER = Requirement("file", "/mnt/data/.notes-app")
APP = AppRef(
    "notes-app",
    "notes-app.service",
    repo="me/notes-app",
    run="npm start",
    requires=(MOUNT, MARKER),
)


def status(present, **kw) -> AppStatus:
    base = {
        "name": "notes-app",
        "unit": "notes-app.service",
        "load": "loaded",
        "active": "inactive",
        "requires_present": present,
        "rev": "abc1234",
    }
    base.update(kw)
    return AppStatus(**base)


# --- declaring ---------------------------------------------------------------------------
def test_requirements_round_trip_through_apps_yaml():
    again = parse_manifest(yaml.safe_load(render_manifest((APP,))))
    assert again[0].requires == (MOUNT, MARKER)


@pytest.mark.parametrize(
    "entry",
    [
        {"kind": "disk", "path": "/mnt/data"},
        {"kind": "mount", "path": "mnt/data"},
        {"kind": "mount", "path": "/mnt/../etc"},
        {"kind": "mount", "path": "/mnt/da ta"},
        {"kind": "mount"},
    ],
)
def test_unsafe_or_unknown_requirements_are_refused(entry):
    with pytest.raises(ConfigError):
        parse_manifest(
            {
                "version": 1,
                "apps": [{"name": "a", "unit": "a.service", "requires": [entry]}],
            }
        )


def test_duplicates_and_non_lists_are_refused():
    base = {"name": "a", "unit": "a.service"}
    twice = {"kind": "mount", "path": "/mnt/data"}
    with pytest.raises(ConfigError, match="Duplicate"):
        parse_manifest({"version": 1, "apps": [{**base, "requires": [twice, twice]}]})
    with pytest.raises(ConfigError):
        parse_manifest(
            {"version": 1, "apps": [{**base, "requires": "mount /mnt/data"}]}
        )


def test_a_line_is_kind_then_path():
    assert parse_requirement_line("mount /mnt/data") == MOUNT
    assert parse_requirement_line("  file   /a/b ") == Requirement("file", "/a/b")
    with pytest.raises(ConfigError):
        parse_requirement_line("/mnt/data")


# --- checking ----------------------------------------------------------------------------
def test_status_script_checks_each_kind_read_only_and_never_creates_anything():
    script = build_status_script((APP,))
    assert (
        "mountpoint -q /mnt/data" in script
        and "[ -f /mnt/data/.notes-app ]" in script
    )
    for forbidden in ("mkdir", "mount /", "touch", "sudo", "rm "):
        assert forbidden not in script
    out = "@@APP notes-app\nLoadState=loaded\nreq0=yes\nreq1=no\n"
    (parsed,) = parse_status(out, (APP,))
    assert parsed.requires_present == (True, False)


def test_missing_ones_are_named_and_unread_ones_are_not_reported():
    assert missing_requirements(APP, status((True, False))) == [MARKER]
    assert missing_requirements(APP, status(())) == []  # not read: no false alarm
    assert missing_requirements(APP, None) == []
    assert missing_lines(APP, status((False, True))) == [
        "Missing on the Pi: mounted disk /mnt/data"
    ]
    assert describe(MARKER) == "file /mnt/data/.notes-app"


def test_the_next_step_waits_for_the_prerequisite_before_start():
    step = next_step(APP, status((False, True)))
    assert step.label == "Prerequisite missing" and "/mnt/data" in step.why
    assert next_step(APP, status((True, True))).label == "Start"
    running = status((False, False), active="active")
    assert next_step(APP, running).label != "Prerequisite missing"


def test_the_wizard_start_step_says_what_is_missing():
    app = AppRef(**{**APP.__dict__, "built": None, "build": None})
    steps = wizard_steps(app, status((False, True)), skipped=frozenset({"env"}))
    start = next(s for s in steps if s.key == "start")
    assert "missing: mounted disk /mnt/data" in start.detail


# --- the forms and the window ------------------------------------------------------------
@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_form_parses_one_prerequisite_per_line(qapp):
    app = app_from_fields(
        "a", "me/a", "", "npm start", "", "mount /mnt/data\nfile /mnt/data/.m"
    )
    assert app.requires == (MOUNT, Requirement("file", "/mnt/data/.m"))
    with pytest.raises(ConfigError):
        app_from_fields("a", "me/a", "", "npm start", "", "disk /mnt/data")
    host = Host("pi", "10.0.0.1", "u", (APP,))
    dialog = InstallAppDialog(host, existing=APP)
    assert (
        dialog.requires_edit.toPlainText()
        == "mount /mnt/data\nfile /mnt/data/.notes-app"
    )
    dialog.requires_edit.setPlainText("mount /mnt/data")
    dialog._accept()
    assert dialog.result_value.requires == (MOUNT,)


def test_a_start_asks_first_only_when_a_prerequisite_is_missing(qapp, monkeypatch):
    from raspi.gui import main_window
    from raspi.gui.fleet_model import Row
    from raspi.gui.main_window import MainWindow
    from raspi.gui.theme import LIGHT
    from raspi.ssh.transport import RunResult

    class Pi:
        def run_script(self, host, script, timeout=20.0):
            return RunResult(True, "")

    asked = []
    monkeypatch.setattr(
        main_window, "confirm_change", lambda *a: asked.append(a[3]) or False
    )
    host = Host("pi", "10.0.0.1", "u", (APP,))
    window = MainWindow([host], Pi(), LIGHT, auto_refresh=False)
    ok = Row(host, APP, status((True, True)))
    bad = Row(host, APP, status((False, True)))
    assert window._start_despite_missing(ok) is True and not asked
    assert window._start_despite_missing(bad) is False  # the developer declined
    assert asked == [["Missing on the Pi: mounted disk /mnt/data"]]
    window.close()


def test_cli_install_app_accepts_repeatable_requirements(monkeypatch):
    from raspi.cli import main as cli

    class Pi:
        def __init__(self):
            self.scripts = []

        def run_script(self, host, script, timeout=20.0):
            self.scripts.append(script)
            return RunResult(True, "key=yes\nstate=missing\ndisk_mb=9000\n")

    from raspi.ssh.transport import RunResult

    pi = Pi()
    host = Host("pi", "10.0.0.1", "u")
    monkeypatch.setattr(cli, "_live_host", lambda name: (host, host))
    monkeypatch.setattr("raspi.cli.provision.SshTransport", lambda: pi)
    args = [
        "host",
        "install-app",
        "pi",
        "a",
        "--repo",
        "me/a",
        "--run",
        "x",
        "--dry-run",
    ]
    good = CliRunner().invoke(cli.app, [*args, "--requires", "mount:/mnt/data"])
    assert good.exit_code == 0
    bad = CliRunner().invoke(cli.app, [*args, "--requires", "disk:/mnt/data"])
    assert bad.exit_code == 1
