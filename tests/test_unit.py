import pytest
from typer.testing import CliRunner

from raspi.core.models import AppRef, EnvFile, Host
from raspi.core.unit import (
    MARKER,
    UnitError,
    build_install_script,
    build_probe_script,
    render_unit,
    systemd_quote,
)
from raspi.services.unit import install_unit, plan_unit
from raspi.ssh.transport import RunResult

APP = AppRef(
    "notes-app",
    "notes-app.service",
    repo="SonSonDev/notes-app",
    build="npm ci && npm run build",
    run="npm start",
    env_files=(EnvFile("default", "/home/pi/apps/notes-app/.env"),),
)
HOST = Host("pi", "10.0.0.1", "pi")


class Pi:
    def __init__(
        self, unit="missing", sudo="yes", node="/home/pi/.nvm/v26/bin", env="yes"
    ):
        self.facts = f"home=/home/pi\nsudo={sudo}\nunit={unit}\nenvfile0={env}\n"
        if node:
            self.facts += f"nodebin={node}\n"
        self.scripts = []

    def run_script(self, host, script, timeout=20.0):
        self.scripts.append(script)
        if "unit=missing" in script:
            return RunResult(True, self.facts)
        return RunResult(True, "installed: notes-app.service\n")


def test_unit_runs_the_command_with_env_files_and_node_in_path():
    text = render_unit(APP, "pi", "/home/pi/apps/notes-app", "/n/bin")
    assert text.startswith(MARKER)
    assert (
        "User=pi" in text
        and "WorkingDirectory=/home/pi/apps/notes-app" in text
    )
    assert "Environment=PATH=/n/bin:/usr/local/bin:/usr/bin:/bin" in text
    assert "EnvironmentFile=/home/pi/apps/notes-app/.env" in text
    assert 'ExecStart=/bin/sh -c "exec npm start"' in text
    assert "Restart=on-failure" in text


def test_command_is_escaped_for_systemd():
    assert systemd_quote('echo "$HOME" 100%') == 'echo \\"$$HOME\\" 100%%'


def test_unit_needs_a_run_command():
    with pytest.raises(UnitError):
        render_unit(AppRef("a", "a.service"), "u", "/x", "")


def test_probe_is_read_only():
    script = build_probe_script(APP)
    for forbidden in ("tee", "systemctl enable", "mkdir", "rm "):
        assert forbidden not in script


def test_install_script_enables_but_does_not_start():
    script = build_install_script(APP, "x")
    assert "systemctl enable notes-app.service" in script
    assert "start" not in script.replace("restart", "")


def test_plan_warns_about_missing_node_and_env_file():
    plan = plan_unit(HOST, Pi(node="", env="no"), APP)
    assert any("No Node" in w for w in plan.warnings)
    assert any("does not exist yet" in w for w in plan.warnings)
    assert "Create" in plan.steps[0]


def test_plan_refuses_a_foreign_unit_and_missing_sudo_but_replaces_ours():
    with pytest.raises(UnitError, match="not overwritten"):
        plan_unit(HOST, Pi(unit="foreign"), APP)
    with pytest.raises(UnitError, match="sudo"):
        plan_unit(HOST, Pi(sudo="no"), APP)
    assert "Replace" in plan_unit(HOST, Pi(unit="ours"), APP).steps[0]


def test_install_runs_the_script_once():
    pi = Pi()
    result = install_unit(HOST, pi, plan_unit(HOST, pi, APP))
    assert result.ok and "not started" in result.message
    assert sum("daemon-reload" in s for s in pi.scripts) == 1


def test_cli_install_unit_dry_run(monkeypatch):
    from raspi.cli import main as cli

    pi = Pi()
    host = Host("pi", "10.0.0.1", "pi", (APP,))
    monkeypatch.setattr(cli, "_live_host", lambda name: (host, host))
    monkeypatch.setattr("raspi.cli.provision.SshTransport", lambda: pi)
    runner = CliRunner()
    out = runner.invoke(
        cli.app, ["host", "install-unit", "pi", "notes-app", "--dry-run"]
    )
    assert out.exit_code == 0 and "ExecStart" in out.output and len(pi.scripts) == 1
    assert runner.invoke(cli.app, ["host", "install-unit", "pi", "nope"]).exit_code == 1
