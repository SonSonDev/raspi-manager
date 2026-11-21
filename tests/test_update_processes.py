"""“Update…” applies each process's `on_update` rule instead of restarting one unit."""

import base64
import os
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication
from typer.testing import CliRunner

from raspi.core.models import AppRef, Host, Process
from raspi.core.update import build_probe_script
from raspi.services.update import apply_update, plan_update
from raspi.ssh.transport import RunResult

OLD, NEW = "a" * 40, "b" * 40
BOT = Process("bot", "vocalog.service", "node bot.js", "keep")
SERVER = Process("server", "vocalog-server.service", "node server.js")
WORKER = Process("worker", "vocalog-worker.service", "node worker.js", "ask")


def app_with(*processes: Process) -> AppRef:
    return AppRef(
        "vocalog",
        processes[0].unit,
        repo="me/vocalog",
        build="npm ci",
        run=processes[0].run,
        built="aaaaaaa",
        processes=processes,
    )


def probe(*active: str, sudo="yes") -> str:
    lines = [
        "workdir=ok",
        f"head={OLD}",
        "dirty=0",
        "local=0",
        "fetch=ok",
        f"target={NEW}",
        "behind=1",
        f"sudo={sudo}",
        "load=loaded",
        f"active={active[0]}",
    ]
    for i, state in enumerate(active[1:], start=1):
        lines += [f"load{i}=loaded", f"active{i}={state}"]
    return "\n".join(lines) + "\n"


class Pi:
    def __init__(self, facts: str, fail_restart_of: str | None = None):
        self.facts, self.fail = facts, fail_restart_of
        self.scripts: list[str] = []
        self.manifest = None

    def run_script(self, host, script, timeout=20.0):
        self.scripts.append(script)
        if 'echo "workdir=ok"' in script:
            return RunResult(True, self.facts)
        if "git checkout --detach" in script:
            return RunResult(True, "rev=bbbbbbb\n")
        if "base64 -d" in script:
            encoded = re.search(r"printf %s '?([A-Za-z0-9+/=]+)'? \|", script)[1]
            self.manifest = base64.b64decode(encoded).decode()
            return RunResult(True, "written\n")
        if "restart" in script and "systemctl" in script:
            if self.fail and self.fail in script:
                return RunResult(True, "exit=1\nActiveState=failed\nSubState=x\n")
            return RunResult(True, "exit=0\nActiveState=active\nSubState=running\n")
        if self.manifest is None:
            return RunResult(True, "@@MISSING\n")
        return RunResult(True, "@@EXISTS\n" + self.manifest)

    def restarted(self) -> list[str]:
        units = ("vocalog-server", "vocalog-worker", "vocalog.service")
        return [
            u
            for s in self.scripts
            if "restart" in s and "systemctl" in s
            for u in units
            if u in s
        ]


def host_of(app: AppRef) -> Host:
    return Host("pi", "10.0.0.1", "pi", (app,))


def test_the_probe_reads_the_state_of_every_process():
    script = build_probe_script(app_with(BOT, SERVER))
    assert "systemctl show vocalog-server.service -p ActiveState" in script
    assert "load1=" in script and "active1=" in script
    assert "restart" not in script and "sudo -n systemctl" not in script


def test_keep_leaves_the_process_alone_and_restart_restarts_the_other():
    app = app_with(BOT, SERVER)
    pi = Pi(probe("active", "active"))
    plan = plan_update(host_of(app), pi, app)
    assert [(p.name, p.restart, p.kept) for p in plan.processes] == [
        ("bot", False, True),
        ("server", True, False),
    ]
    assert "Restart vocalog-server.service" in plan.steps
    assert "vocalog.service is not restarted (see the notes)" in plan.steps
    assert any("keeps the old code" in n for n in plan.warnings)
    result = apply_update(host_of(app), pi, plan)
    assert result.ok and pi.restarted() == ["vocalog-server"]
    assert any("vocalog.service was kept running" in m for m in result.messages)
    assert any("Restarted vocalog-server.service" in m for m in result.messages)
    assert "built: bbbbbbb" in pi.manifest  # the new build is recorded once


def test_a_stopped_process_stays_stopped_and_a_missing_sudo_restarts_nothing():
    app = app_with(SERVER, WORKER)
    stopped = plan_update(host_of(app), Pi(probe("active", "inactive")), app)
    assert [p.restart for p in stopped.processes] == [True, False]
    assert any("vocalog-worker.service is not running" in n for n in stopped.warnings)
    nosudo = plan_update(host_of(app), Pi(probe("active", "active", sudo="no")), app)
    assert not nosudo.restart and sum("sudo" in n for n in nosudo.warnings) == 2


def test_ask_starts_ticked_and_the_choice_decides():
    app = app_with(SERVER, WORKER)
    pi = Pi(probe("active", "active"))
    plan = plan_update(host_of(app), pi, app)
    assert [p.name for p in plan.processes if p.ask] == ["worker"]
    plan.processes[1].restart = False
    apply_update(host_of(app), pi, plan)
    assert pi.restarted() == ["vocalog-server"]


def test_a_failed_restart_names_the_unit_and_keeps_what_was_done():
    app = app_with(SERVER, WORKER)
    pi = Pi(probe("active", "active"), fail_restart_of="vocalog-worker")
    result = apply_update(host_of(app), pi, plan_update(host_of(app), pi, app))
    assert not result.ok
    assert "Restart of vocalog-worker.service failed" in result.messages[-1]
    assert any("Restarted vocalog-server.service" in m for m in result.messages)


def test_an_app_without_processes_keeps_its_old_wording_and_behaviour():
    app = AppRef("n", "n.service", repo="me/n", build="make", run="x", built="aaaaaaa")
    pi = Pi(probe("active"))
    plan = plan_update(host_of(app), pi, app)
    assert plan.restart and "Restart n.service" in plan.steps
    result = apply_update(host_of(app), pi, plan)
    assert result.messages[-1] == "Restarted (active)"


def test_cancelling_every_restart_and_naming_one_to_keep():
    app = app_with(SERVER, WORKER)
    plan = plan_update(host_of(app), Pi(probe("active", "active")), app)
    plan.restart = False
    assert not plan.restart and all("not restarted" in s for s in plan.steps[-3:-1])


def test_cli_keep_names_a_process_and_refuses_an_unknown_one(monkeypatch):
    from raspi.cli import main as cli

    app = app_with(SERVER, WORKER)
    host = host_of(app)
    pi = Pi(probe("active", "active"))
    monkeypatch.setattr(cli, "_live_host", lambda name: (host, host))
    monkeypatch.setattr("raspi.cli.update.SshTransport", lambda: pi)
    run = CliRunner()
    base = ["host", "update", "pi", "vocalog", "--yes"]
    out = run.invoke(cli.app, [*base, "--keep", "worker"])
    assert out.exit_code == 0 and pi.restarted() == ["vocalog-server"]
    bad = run.invoke(cli.app, [*base, "--keep", "nope"])
    assert bad.exit_code == 1 and "no process called nope" in bad.output


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _gui(qapp, monkeypatch, ticked):
    from raspi.gui import update_actions
    from raspi.gui.main_window import MainWindow
    from raspi.gui.theme import LIGHT

    app = app_with(SERVER, WORKER)
    pi = Pi(probe("active", "active"))
    asked = []

    def options(*args):
        asked.append(args)
        return True, [ticked]

    monkeypatch.setattr(update_actions, "confirm_with_options", options)
    monkeypatch.setattr(update_actions, "submit", lambda fn, ok, bad=None: ok(fn()))
    for name in ("information", "critical"):
        monkeypatch.setattr(update_actions.QMessageBox, name, lambda *a: None)
    window = MainWindow([host_of(app)], pi, LIGHT, auto_refresh=False)
    window._on_update_planned((host_of(app), plan_update(host_of(app), pi, app), True))
    window.close()
    return pi, asked


def test_gui_asks_once_per_ask_process_and_follows_the_tick(qapp, monkeypatch):
    pi, asked = _gui(qapp, monkeypatch, ticked=False)
    assert asked[0][-1] == ["Restart vocalog-worker.service after the update"]
    assert pi.restarted() == ["vocalog-server"]
    pi, _ = _gui(qapp, monkeypatch, ticked=True)
    assert pi.restarted() == ["vocalog-server", "vocalog-worker"]
