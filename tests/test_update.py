import base64
import os
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
import yaml
from PySide6.QtWidgets import QApplication
from typer.testing import CliRunner

from raspi.config.loader import parse_manifest
from raspi.core.models import AppRef, Host
from raspi.core.update import (
    UpdateError,
    build_probe_script,
    build_update_script,
    check_ref,
    rollback_target,
)
from raspi.services.update import apply_update, plan_update
from raspi.ssh.transport import RunResult

OLD = "a" * 40
NEW = "b" * 40
APP = AppRef(
    "notes-app",
    "notes-app.service",
    repo="me/notes-app",
    build="npm ci && npm run build",
    run="npm start",
    built="aaaaaaa",
)
HOST = Host("pi", "10.0.0.1", "pi", (APP,))


def facts(**kw) -> str:
    base = {
        "workdir": "ok",
        "head": OLD,
        "dirty": "0",
        "local": "0",
        "fetch": "ok",
        "target": NEW,
        "behind": "2",
        "sudo": "yes",
        "load": "loaded",
        "active": "active",
    }
    base.update(kw)
    lines = [f"{k}={v}" for k, v in base.items()]
    lines += ["log=bbbbbbb Fix login", "log=ccccccc Add the thing"]
    return "\n".join(lines) + "\n"


class Pi:
    def __init__(self, probe: str | None = None, build_ok: bool = True):
        self.probe = probe or facts()
        self.build_ok, self.scripts, self.manifest = build_ok, [], None

    def run_script(self, host, script, timeout=20.0):
        self.scripts.append(script)
        if 'echo "workdir=ok"' in script:
            return RunResult(True, self.probe)
        if "git checkout --detach" in script:
            if not self.build_ok:
                return RunResult(False, "", "npm ERR! build failed")
            return RunResult(True, "rev=bbbbbbb\n")
        if "base64 -d" in script:
            encoded = re.search(r"printf %s '?([A-Za-z0-9+/=]+)'? \|", script)[1]
            self.manifest = base64.b64decode(encoded).decode()
            return RunResult(True, "written\n")
        if "systemctl" in script and "restart" in script:
            return RunResult(True, "exit=0\nActiveState=active\nSubState=running\n")
        if "apps.yaml" in script:
            if self.manifest is None:
                return RunResult(True, "@@MISSING\n")
            return RunResult(True, "@@EXISTS\n" + self.manifest)
        return RunResult(True, "")

    def saved(self) -> AppRef:
        return parse_manifest(yaml.safe_load(self.manifest))[0]


def test_probe_only_fetches_and_never_changes_the_working_copy():
    for script in (
        build_probe_script(APP),
        build_probe_script(APP, ref="v1.2"),
        build_probe_script(APP, sha="abc1234"),
    ):
        for forbidden in (
            "checkout",
            "reset",
            "merge",
            "pull",
            "rm ",
            "sudo systemctl",
            ">>",
        ):
            assert forbidden not in script
        assert "--untracked-files=no" in script and "git fetch --quiet --tags" in script


def test_update_script_checks_out_one_exact_commit_then_builds():
    script = build_update_script(APP, NEW)
    assert f"git checkout --detach --quiet {NEW}" in script
    assert script.index("git checkout") < script.index("npm ci") < script.index("rev=")
    assert "sudo" not in script
    with pytest.raises(UpdateError):
        build_update_script(APP, "abc1234")  # a short hash is ambiguous


@pytest.mark.parametrize("bad", ["main; rm -rf /", "-x", "a b", "$(id)"])
def test_unsafe_refs_are_refused(bad):
    with pytest.raises(UpdateError):
        check_ref(bad)
    assert check_ref(" v1.2/x ") == "v1.2/x" and check_ref(None) == ""


def test_rollback_goes_to_the_last_build_after_a_failed_update_else_to_previous():
    ok = AppRef("a", "a.service", built="bbbbbbb", previous="aaaaaaa")
    assert rollback_target(ok, NEW) == "aaaaaaa"
    failed = AppRef("a", "a.service", built="aaaaaaa", previous="9999999")
    assert rollback_target(failed, NEW) == "aaaaaaa"
    assert rollback_target(AppRef("a", "a.service"), NEW) == ""


@pytest.mark.parametrize(
    ("probe", "message"),
    [
        (facts(workdir="missing"), "Install app"),
        (facts(fetch="failed"), "Deploy key"),
        (facts(target=""), "Unknown ref"),
        (facts(dirty="3"), "uncommitted"),
        (facts(local="1"), "only on"),
    ],
)
def test_plan_refuses_with_the_reason(probe, message):
    with pytest.raises(UpdateError, match=message):
        plan_update(HOST, Pi(probe), APP)
    with pytest.raises(UpdateError, match="no repo"):
        plan_update(HOST, Pi(), AppRef("a", "a.service"))


def test_plan_lists_incoming_commits_and_restarts_only_a_running_app():
    plan = plan_update(HOST, Pi(), APP)
    assert plan.commits == ["bbbbbbb Fix login", "ccccccc Add the thing"]
    assert plan.restart and any("Restart" in s for s in plan.steps)
    stopped = plan_update(HOST, Pi(facts(active="inactive")), APP)
    assert not stopped.restart and any("stays stopped" in n for n in stopped.warnings)
    nosudo = plan_update(HOST, Pi(facts(sudo="no")), APP)
    assert not nosudo.restart and any("sudo" in n for n in nosudo.warnings)
    nounit = plan_update(HOST, Pi(facts(load="not-found", active="inactive")), APP)
    assert any("no unit" in n for n in nounit.warnings)


def test_nothing_to_do_when_already_there_and_built():
    same = plan_update(
        HOST, Pi(facts(head=NEW)), AppRef(**{**APP.__dict__, "built": "bbbbbbb"})
    )
    assert same.nothing_to_do
    unbuilt = plan_update(
        HOST, Pi(facts(head=NEW)), AppRef(**{**APP.__dict__, "built": None})
    )
    assert not unbuilt.nothing_to_do


def test_apply_builds_records_built_and_previous_then_restarts():
    pi = Pi()
    result = apply_update(HOST, pi, plan_update(HOST, pi, APP))
    assert result.ok and "Restarted" in result.messages[-1]
    saved = pi.saved()
    assert saved.built == "bbbbbbb" and saved.previous == OLD[:7]
    assert saved.run == "npm start" and saved.repo == "me/notes-app"


def test_failed_build_changes_nothing_else_and_points_to_the_rollback():
    pi = Pi(build_ok=False)
    result = apply_update(HOST, pi, plan_update(HOST, pi, APP))
    assert not result.ok and "Build failed" in result.messages[0]
    assert "return" in result.messages[1] and pi.manifest is None
    assert not any("restart" in s and "systemctl" in s for s in pi.scripts)


def test_rollback_returns_to_the_previous_revision_and_forgets_it():
    app = AppRef(**{**APP.__dict__, "built": "bbbbbbb", "previous": "aaaaaaa"})
    host = Host("pi", "10.0.0.1", "pi", (app,))
    pi = Pi(facts(head=NEW, target=OLD))
    plan = plan_update(host, pi, app, rollback=True)
    assert plan.rollback and plan.new == OLD and "Go back to" in plan.steps[0]
    result = apply_update(host, pi, plan)
    assert result.ok and pi.saved().previous is None
    with pytest.raises(UpdateError, match="no earlier"):
        plan_update(
            host,
            Pi(),
            AppRef(**{**app.__dict__, "built": None, "previous": None}),
            rollback=True,
        )


def test_cli_update_dry_run_then_nothing_runs(monkeypatch):
    from raspi.cli import main as cli

    pi = Pi()
    monkeypatch.setattr(cli, "_live_host", lambda name: (HOST, HOST))
    monkeypatch.setattr("raspi.cli.update.SshTransport", lambda: pi)
    out = CliRunner().invoke(
        cli.app, ["host", "update", "pi", "notes-app", "--dry-run"]
    )
    assert out.exit_code == 0 and "git checkout --detach" in out.output
    assert "Fix login" in out.output and len(pi.scripts) == 1
    bad = CliRunner().invoke(cli.app, ["host", "update", "pi", "nope"])
    assert bad.exit_code == 1


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_gui_update_flow_runs_after_confirmation(qapp, monkeypatch):
    from raspi.gui import update_actions
    from raspi.gui.main_window import MainWindow
    from raspi.gui.theme import LIGHT

    shown = []
    monkeypatch.setattr(update_actions, "submit", lambda fn, ok, bad=None: ok(fn()))
    monkeypatch.setattr(
        update_actions.QMessageBox, "information", lambda *a: shown.append(a[2])
    )
    monkeypatch.setattr(
        update_actions.QMessageBox, "critical", lambda *a: shown.append(a[2])
    )
    monkeypatch.setattr(MainWindow, "_confirm_change", lambda *a: True)
    pi = Pi()
    window = MainWindow([HOST], pi, LIGHT, auto_refresh=False)
    window._on_update_planned((HOST, plan_update(HOST, pi, APP)))
    assert any("git checkout --detach" in s for s in pi.scripts)
    assert shown and "Recorded" in shown[0]
    window.close()
