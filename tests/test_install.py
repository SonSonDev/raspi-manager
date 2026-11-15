import base64
import re

import pytest
from typer.testing import CliRunner

from raspi.core.install import InstallError, build_install_script, build_probe_script
from raspi.core.models import AppRef, Host
from raspi.services.install import install_app, plan_install
from raspi.ssh.transport import RunResult

APP = AppRef(
    "notes-app",
    "notes-app.service",
    repo="SonSonDev/notes-app",
    build="npm ci && npm run build",
    run="npm start",
)
HOST = Host("pi", "10.0.0.1", "pi")


class Pi:
    def __init__(self, state="missing", key="yes", disk="9000", fail_build=False):
        self.facts = f"key={key}\nstate={state}\ndisk_mb={disk}\n"
        self.fail_build, self.scripts, self.manifest = fail_build, [], None

    def run_script(self, host, script, timeout=20.0):
        self.scripts.append(script)
        if "state=" in script:
            return RunResult(True, self.facts)
        if "git rev-parse" in script:
            if self.fail_build:
                return RunResult(False, "", "npm ERR! build failed")
            return RunResult(True, "rev=abc1234\n")
        if "base64 -d" in script:
            encoded = re.search(r"printf %s '?([A-Za-z0-9+/=]+)'? \|", script)[1]
            self.manifest = base64.b64decode(encoded).decode()
            return RunResult(True, "written\n")
        if self.manifest is None:
            return RunResult(True, "@@MISSING\n")
        return RunResult(True, "@@EXISTS\n" + self.manifest)


def test_probe_is_read_only():
    script = build_probe_script(APP)
    for forbidden in ("git clone", "mkdir", "rm ", "sudo", ">>"):
        assert forbidden not in script


def test_clone_goes_through_the_alias_and_never_prompts():
    script = build_install_script(APP, "missing")
    assert "git@github-notes-app:SonSonDev/notes-app.git" in script
    assert "GIT_TERMINAL_PROMPT=0" in script and "sudo" not in script
    assert script.index("git clone") < script.index("npm ci")
    assert script.index("nvm.sh") < script.index("npm ci")


def test_existing_working_copy_is_not_touched():
    script = build_install_script(APP, "repo")
    assert "git clone" not in script and "git pull" not in script
    assert "checkout" not in script


def test_foreign_folder_and_missing_repo_are_refused():
    with pytest.raises(InstallError):
        build_install_script(APP, "other")
    with pytest.raises(InstallError):
        build_install_script(AppRef("a", "a.service"), "missing")


def test_plan_needs_the_deploy_key_and_some_disk():
    with pytest.raises(InstallError, match="deploy-key"):
        plan_install(HOST, Pi(key="no"), APP)
    with pytest.raises(InstallError, match="free"):
        plan_install(HOST, Pi(disk="100"), APP)
    assert plan_install(HOST, Pi(), APP).state == "missing"


def test_install_builds_then_records_the_app():
    pi = Pi()
    result = install_app(HOST, pi, plan_install(HOST, pi, APP))
    assert result.ok and result.rev == "abc1234" and "inventory" in result.message


def test_failed_build_is_reported_and_not_recorded():
    pi = Pi(fail_build=True)
    result = install_app(HOST, pi, plan_install(HOST, pi, APP))
    assert not result.ok and "build failed" in result.message
    assert not any("base64" in s for s in pi.scripts)


def test_cli_install_app_dry_run(monkeypatch):
    from raspi.cli import main as cli

    pi = Pi()
    monkeypatch.setattr(cli, "_live_host", lambda name: (HOST, HOST))
    monkeypatch.setattr("raspi.cli.provision.SshTransport", lambda: pi)
    out = CliRunner().invoke(
        cli.app,
        [
            "host",
            "install-app",
            "pi",
            "notes-app",
            "--repo",
            "SonSonDev/notes-app",
            "--build",
            "npm ci && npm run build",
            "--dry-run",
        ],
    )
    assert out.exit_code == 0 and "git clone" in out.output
    assert len(pi.scripts) == 1  # only the probe ran
    bad = CliRunner().invoke(
        cli.app, ["host", "install-app", "pi", "x", "--repo", "nope"]
    )
    assert bad.exit_code == 1


def test_reinstall_updates_the_entry_and_keeps_its_env_files():
    from dataclasses import replace

    from raspi.core.models import EnvFile

    declared = replace(
        APP,
        build=None,
        run=None,
        env_files=(EnvFile("default", "/home/pi/x/.env"),),
    )
    host = Host("pi", "10.0.0.1", "pi", (declared,))
    pi = Pi(state="repo")
    first = install_app(host, pi, plan_install(host, pi, APP))
    assert first.ok and "updated in the inventory" in first.message
    saved = pi.manifest
    assert "npm ci && npm run build" in saved and "run: npm start" in saved
    assert "/home/pi/x/.env" in saved
