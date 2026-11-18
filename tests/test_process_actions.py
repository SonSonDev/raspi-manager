"""Whole-app actions with several processes: start all, restart all but the kept ones."""

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication, QMessageBox

from raspi.config import envstore
from raspi.core.models import AppRef, AppStatus, EnvFile, Host, Process, ProcessStatus
from raspi.core.processes import restart_targets
from raspi.core.wizard import START, wizard_steps
from raspi.gui.env_dialog import EnvDialog
from raspi.services.actions import run_processes
from raspi.ssh.transport import RunResult

BOT = Process("bot", "vocalog.service", "node bot.js", "keep")
SERVER = Process("server", "vocalog-server.service", "node server.js")
WORKER = Process("worker", "vocalog-worker.service", "node worker.js", "ask")
ENV = EnvFile("default", "/home/u/apps/vocalog/.env")
APP = AppRef(
    "vocalog",
    "vocalog.service",
    repo="me/vocalog",
    run="node bot.js",
    env_files=(ENV,),
    processes=(BOT, SERVER, WORKER),
)
HOST = Host("pi", "10.0.0.1", "u", (APP,))


class Pi:
    def __init__(self, fail: str | None = None):
        self.fail, self.scripts = fail, []

    def run_script(self, host, script, timeout=20.0):
        self.scripts.append(script)
        if "sudo -n systemctl" in script:
            if self.fail and self.fail in script:
                return RunResult(True, "exit=1\nActiveState=failed\nSubState=x\n")
            return RunResult(True, "exit=0\nActiveState=active\nSubState=running\n")
        if "base64 -d" in script:
            return RunResult(True, "written: 1 lines\n")
        return RunResult(True, "@@EXISTS\nPORT=1\n")

    def units(self, verb: str) -> list[str]:
        return [
            u
            for s in self.scripts
            if f"systemctl {verb}" in s and "sudo -n systemctl" in s
            for u in ("vocalog-server", "vocalog-worker", "vocalog.service")
            if u in s
        ]


def test_keep_is_the_only_rule_a_whole_app_restart_leaves_alone():
    restarted, kept = restart_targets(APP)
    assert [p.name for p in restarted] == ["server", "worker"]
    assert [p.name for p in kept] == ["bot"]
    plain = AppRef("n", "n.service", run="x")
    assert [p.unit for p in restart_targets(plain)[0]] == ["n.service"]


def test_one_process_gives_its_own_result_unchanged():
    result = run_processes(HOST, AppRef("n", "n.service", run="x"), "start", Pi())
    assert result.ok and result.state == "active"


def test_every_process_in_order_and_the_first_failure_names_its_unit():
    pi = Pi()
    result = run_processes(HOST, APP, "start", pi)
    assert result.ok and pi.units("start") == [
        "vocalog.service",
        "vocalog-server",
        "vocalog-worker",
    ]
    bad = Pi(fail="vocalog-server")
    result = run_processes(HOST, APP, "start", bad)
    assert not result.ok and result.message.startswith("vocalog-server.service:")
    assert "vocalog-worker" not in "".join(bad.scripts)  # stops at the failure


def test_the_wizard_start_step_waits_for_every_process():
    def live(*states):
        procs = tuple(
            ProcessStatus(p.name, p.unit, load="loaded", active=a)
            for p, a in zip(APP.processes, states, strict=True)
        )
        return AppStatus(
            "vocalog",
            "vocalog.service",
            load="loaded",
            active=states[0],
            rev="abc1234",
            key_present=True,
            env_present=(True,),
            processes=procs,
        )

    done = AppRef(**{**APP.__dict__, "built": None})
    steps = wizard_steps(done, live("active", "inactive", "active"), verified=True)
    start = next(s for s in steps if s.key == START)
    assert start.state != "done" and start.detail == "not running: server"
    steps = wizard_steps(done, live("active", "active", "active"), verified=True)
    assert next(s for s in steps if s.key == START).state == "done"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def pump(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        QCoreApplication.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_push_and_restart_leaves_the_kept_process_running(qapp, monkeypatch, tmp_path):
    monkeypatch.setenv("RASPI_HOME", str(tmp_path))
    envstore.save_local("pi", "vocalog", {"PORT": "2"})
    pi = Pi()
    dialog = EnvDialog(HOST, APP, pi, None)
    assert pump(lambda: dialog._remote is not None)
    shown = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: shown.append(self.text()))
    monkeypatch.setattr(
        QMessageBox,
        "clickedButton",
        lambda self: next(b for b in self.buttons() if b.text().startswith("Push")),
    )
    dialog.push_to_pi(restart=True)
    assert pump(lambda: pi.units("restart"))
    assert pump(lambda: len(pi.units("restart")) == 2)
    assert pi.units("restart") == ["vocalog-server", "vocalog-worker"]
    text = shown[0]
    assert "Then restarts vocalog-server.service, vocalog-worker.service." in text
    assert "Left running with the old values" in text and "vocalog.service" in text
    dialog._dirty = False
    dialog.close()
