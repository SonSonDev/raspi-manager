"""Declaring processes: one text format for the install form and `install-app --process`."""

import base64
import os
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
import yaml
from PySide6.QtWidgets import QApplication
from typer.testing import CliRunner

from raspi.config.loader import ConfigError, parse_manifest, parse_process_line
from raspi.core.models import AppRef, Host, Process
from raspi.core.processes import format_process_line
from raspi.gui.install_dialog import InstallAppDialog, app_from_fields
from raspi.services.install import install_app, plan_install
from raspi.ssh.transport import RunResult


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize(
    ("line", "entry"),
    [
        ("bot:node bot.js", {"name": "bot", "run": "node bot.js"}),
        (
            "bot:node bot.js:keep",
            {"name": "bot", "run": "node bot.js", "on_update": "keep"},
        ),
        (
            "bot=vocalog.service:node bot.js:ask",
            {
                "name": "bot",
                "run": "node bot.js",
                "unit": "vocalog.service",
                "on_update": "ask",
            },
        ),
        # a colon inside the command is not a rule
        (
            "web:node s.js --host 0.0.0.0:8080",
            {"name": "web", "run": "node s.js --host 0.0.0.0:8080"},
        ),
        (
            "web:node s.js --bind a:b:keep",
            {"name": "web", "run": "node s.js --bind a:b", "on_update": "keep"},
        ),
    ],
)
def test_a_process_line_is_read_like_it_is_written(line, entry):
    assert parse_process_line(line) == entry


@pytest.mark.parametrize("line", ["bot", "bot:", ":node x"])
def test_a_malformed_process_line_is_refused_or_caught_by_the_checks(line):
    with pytest.raises(ConfigError):
        entry = parse_process_line(line)
        parse_manifest(
            {
                "version": 1,
                "apps": [{"name": "a", "unit": "a.service", "processes": [entry]}],
            }
        )


def test_the_text_round_trips():
    app = AppRef("vocalog", "vocalog.service")
    for process in (
        Process("bot", "vocalog.service", "node bot.js", "keep"),
        Process("server", "vocalog-server.service", "node s.js"),
    ):
        entry = parse_process_line(format_process_line(app, process))
        entry.setdefault("unit", f"vocalog-{process.name}.service")
        assert (entry["name"], entry["unit"], entry["run"]) == (
            process.name,
            process.unit,
            process.run,
        )


LINES = "bot:node bot.js:keep\nserver:node server.js"


def test_processes_replace_run_and_the_first_keeps_the_apps_unit():
    app = app_from_fields(
        "vocalog",
        "me/vocalog",
        "",
        "ignored",
        "",
        "",
        LINES,
        existing_unit="vocalog.service",
    )
    assert [(p.name, p.unit, p.on_update) for p in app.processes] == [
        ("bot", "vocalog.service", "keep"),
        ("server", "vocalog-server.service", "restart"),
    ]
    assert (app.unit, app.run) == ("vocalog.service", "node bot.js")


def test_a_new_app_gets_default_unit_names():
    app = app_from_fields("vocalog", "me/vocalog", "", "", "", "", LINES)
    assert app.unit == "vocalog-bot.service"


def test_going_back_to_one_run_command_keeps_the_existing_unit():
    app = app_from_fields(
        "vocalog",
        "me/vocalog",
        "",
        "npm start",
        "",
        existing_unit="vocalog-bot.service",
    )
    assert app.processes == () and app.unit == "vocalog-bot.service"


def test_the_form_is_prefilled_and_accepts_processes_without_a_run_command(qapp):
    existing = parse_manifest(
        {
            "version": 1,
            "apps": [
                {
                    "name": "vocalog",
                    "repo": "me/vocalog",
                    "processes": [
                        {
                            "name": "bot",
                            "run": "node bot.js",
                            "unit": "vocalog.service",
                            "on_update": "keep",
                        },
                        {"name": "server", "run": "node s.js"},
                    ],
                }
            ],
        }
    )[0]
    dialog = InstallAppDialog(Host("pi", "h", "pi", (existing,)), existing=existing)
    assert dialog.processes_edit.toPlainText().splitlines() == [
        "bot=vocalog.service:node bot.js:keep",
        "server:node s.js",
    ]
    dialog.run_edit.setText("")
    dialog._accept()
    assert dialog.result_value == existing  # nothing changed, nothing lost


def test_the_form_still_needs_a_run_command_or_processes(qapp):
    dialog = InstallAppDialog(Host("pi", "h", "pi"))
    dialog.name_edit.setText("new")
    dialog.repo_edit.setText("me/new")
    dialog._accept()
    assert dialog.result_value is None and "or processes" in dialog.error.text()


class Pi:
    def __init__(self):
        self.scripts: list[str] = []
        self.manifest = None

    def run_script(self, host, script, timeout=20.0):
        self.scripts.append(script)
        if 'echo "sudo=' in script:
            return RunResult(
                True,
                "home=/home/pi\nsudo=yes\nunit=ours\nunit1=missing\nnodebin=/n/bin\n",
            )
        if "state=" in script:
            return RunResult(True, "key=yes\nstate=repo\ndisk_mb=9000\n")
        if "git rev-parse" in script:
            return RunResult(True, "rev=abc1234\n")
        if "daemon-reload" in script:
            return RunResult(True, "installed\n")
        if "base64 -d" in script:
            encoded = re.search(r"printf %s '?([A-Za-z0-9+/=]+)'? \|", script)[1]
            self.manifest = base64.b64decode(encoded).decode()
            return RunResult(True, "written\n")
        if self.manifest is None:
            return RunResult(True, "@@MISSING\n")
        return RunResult(True, "@@EXISTS\n" + self.manifest)


KNOWN = AppRef("vocalog", "vocalog.service", repo="me/vocalog", run="node bot.js")


def test_adopting_the_running_unit_as_the_first_process_end_to_end():
    host = Host("pi", "10.0.0.1", "pi", (KNOWN,))
    app = app_from_fields(
        "vocalog", "me/vocalog", "", "", "", "", LINES, existing_unit=KNOWN.unit
    )
    pi = Pi()
    plan = plan_install(host, pi, app)
    assert plan.unit is not None and plan.unit.existing
    result = install_app(host, pi, plan, with_unit=True)
    assert result.ok, result.message
    saved = yaml.safe_load(pi.manifest)["apps"][0]
    assert saved["unit"] == "vocalog.service"
    assert [p["name"] for p in saved["processes"]] == ["bot", "server"]
    assert "systemctl restart" not in "".join(pi.scripts)  # adopting restarts nothing


def test_cli_process_option_adopts_the_known_unit_and_shows_the_units(monkeypatch):
    from raspi.cli import main as cli

    host = Host("pi", "10.0.0.1", "pi", (KNOWN,))
    pi = Pi()
    monkeypatch.setattr(cli, "_live_host", lambda name: (host, host))
    monkeypatch.setattr("raspi.cli.provision.SshTransport", lambda: pi)
    args = ["host", "install-app", "pi", "vocalog", "--repo", "me/vocalog", "--dry-run"]
    args += ["--process", "bot:node bot.js:keep", "--process", "server:node server.js"]
    out = CliRunner().invoke(cli.app, args)
    assert out.exit_code == 0, out.output
    assert "vocalog.service" in out.output and "vocalog-server.service" in out.output
    bad = CliRunner().invoke(cli.app, [*args[:-2], "--process", "x"])
    assert bad.exit_code == 1
