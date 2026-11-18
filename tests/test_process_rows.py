"""One table row per process of an app, and Start/Stop/Restart/Logs on the selected one."""

import os
import subprocess
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from raspi.core.models import AppRef, EnvFile, Host, Process
from raspi.core.remote import build_status_script, parse_status
from raspi.core.stage import READY, process_next
from raspi.gui.fleet_model import COLUMNS
from raspi.gui.main_window import MainWindow
from raspi.gui.theme import LIGHT
from raspi.ssh.transport import RunResult

BOT = Process("bot", "vocalog.service", "node bot.js", "keep")
SERVER = Process("server", "vocalog-server.service", "node server.js")
APP = AppRef(
    "vocalog",
    "vocalog.service",
    repo="me/vocalog",
    run="node bot.js",
    env_files=(EnvFile("default", "/home/pi/apps/vocalog/.env"),),
    processes=(BOT, SERVER),
)
PLAIN = AppRef("notes", "notes.service", path="/n")
HOST = Host("pi", "10.0.0.1", "pi", (APP, PLAIN))

STATUS = """@@APP vocalog
LoadState=loaded
ActiveState=active
SubState=running
NRestarts=0
key=yes
envfile0=yes
rev=abc1234
dirty=0
headtime=2000
@@PROC bot
LoadState=loaded
ActiveState=active
SubState=running
NRestarts=3
ActiveEnterTimestamp=Mon 2026-11-16 10:00:00 UTC
started=1000
@@PROC server
LoadState=loaded
ActiveState=inactive
SubState=dead
NRestarts=0
started=0
@@APP notes
LoadState=loaded
ActiveState=active
SubState=running
NRestarts=1
"""


class Pi:
    def __init__(self):
        self.scripts: list[str] = []

    def run_script(self, host, script, timeout=20.0):
        self.scripts.append(script)
        if "sudo -n systemctl" in script:
            return RunResult(True, "exit=0\nActiveState=active\nSubState=running\n")
        if "journalctl" in script:
            return RunResult(True, "log line")
        return RunResult(True, STATUS)

    def sent(self, kind: str) -> list[str]:
        return [s for s in self.scripts if kind in s]


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def pump(condition, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        QCoreApplication.processEvents()
        if condition():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def window(qapp, monkeypatch):
    pi = Pi()
    w = MainWindow([HOST], pi, LIGHT, auto_refresh=False)
    monkeypatch.setattr(MainWindow, "_confirm", lambda self, row, action: True)
    w.show()
    w.refresh()
    assert pump(lambda: not w._inflight)
    yield w, pi
    w.close()


def text(w, row: int, column: str) -> str:
    return w.model.index(row, COLUMNS.index(column)).data()


def test_an_app_with_processes_gets_one_row_each_and_a_plain_app_stays_one(window):
    w, _ = window
    assert [r.key for r in w.model.rows] == [
        ("pi", "vocalog", "bot"),
        ("pi", "vocalog", "server"),
        ("pi", "notes"),
    ]
    assert [text(w, i, "App") for i in range(3)] == [
        "vocalog · bot",
        "vocalog · server",
        "notes",
    ]


def test_each_row_shows_its_own_state_restarts_and_next_step(window):
    w, _ = window
    assert [w.model.state_at(i) for i in range(3)] == ["running", "stopped", "running"]
    assert [text(w, i, "Restarts") for i in range(3)] == ["3", "0", "1"]
    assert text(w, 0, "Next step") == "Old code (Restart)"  # started 1000 < HEAD 2000
    assert text(w, 1, "Next step") == "Start"
    assert text(w, 0, "Version") == text(w, 1, "Version") == "abc1234"


def test_buttons_follow_the_selected_process(window):
    w, _ = window
    w.select_key(("pi", "vocalog", "bot"))
    assert w.btn_restart.isEnabled() and not w.btn_start.isEnabled()
    w.select_key(("pi", "vocalog", "server"))
    assert w.btn_start.isEnabled() and not w.btn_restart.isEnabled()


def test_restart_acts_on_the_selected_process_only(window):
    w, pi = window
    w.select_key(("pi", "vocalog", "bot"))
    w.act("restart")
    assert pump(lambda: pi.sent("sudo -n systemctl"))
    sent = pi.sent("sudo -n systemctl")
    assert len(sent) == 1 and "vocalog.service" in sent[0]
    assert "vocalog-server.service" not in sent[0]
    assert pump(lambda: "✓ restart vocalog bot" in w.status_label.text())


def test_start_and_logs_use_the_unit_of_the_selected_process(window):
    w, pi = window
    w.select_key(("pi", "vocalog", "server"))
    w.act("start")
    assert pump(lambda: pi.sent("sudo -n systemctl"))
    assert "vocalog-server.service" in pi.sent("sudo -n systemctl")[0]
    w.show_logs()
    assert pump(lambda: w.logs_view.toPlainText() == "log line")
    assert "Logs: vocalog server on pi" in w.logs_dock.windowTitle()
    assert "vocalog-server.service" in pi.sent("journalctl")[-1]


def test_a_pending_action_marks_only_its_own_row(window):
    w, _ = window
    w.model.set_pending(("pi", "vocalog", "bot"), "restarting")
    assert [w.model.state_at(i) for i in range(3)] == ["working", "stopped", "running"]


def live(app: AppRef, **kw):
    return parse_status(STATUS, (app,))[0]


def test_process_next_names_what_each_process_needs():
    status = live(APP)
    bot, server = status.processes
    assert process_next(APP, status, BOT, bot).label == "Old code (Restart)"
    assert process_next(APP, status, SERVER, server).label == "Start"
    bot.started = 3000  # started after the last update
    assert process_next(APP, status, BOT, bot).label == READY
    bot.started = 0  # unknown: never claims old code
    assert process_next(APP, status, BOT, bot).label == READY


def test_process_next_sends_app_level_blockers_to_every_row():
    status = live(APP)
    status.env_present = (False,)
    for process, state in zip(APP.processes, status.processes, strict=True):
        assert process_next(APP, status, process, state).label == "Push environment…"


def test_process_next_without_a_unit_or_with_a_stale_one():
    status = live(APP)
    _, server = status.processes
    server.load = "not-found"
    step = process_next(APP, status, SERVER, server)
    assert step.label == "Install unit…" and "vocalog-server.service" in step.why
    server.load, server.unit_ours, server.unit_exec = "loaded", True, "/bin/sh -c x"
    assert process_next(APP, status, SERVER, server).label.startswith(
        "Unit out of date"
    )


def test_the_status_script_reads_when_the_process_started_and_when_head_moved(
    tmp_path,
):
    """Run the real lines with a fake systemctl and a real git folder."""
    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(["git", "init", "-q", str(work)], check=True)
    shim = tmp_path / "bin"
    shim.mkdir()
    (shim / "systemctl").write_text(
        '#!/bin/sh\ncase "$*" in *--value*) echo "Mon 2020-01-06 10:00:00 UTC";; '
        "*) echo ActiveState=active;; esac\n",
        newline="\n",
    )
    app = AppRef(
        "vocalog",
        "vocalog.service",
        path=work.as_posix(),
        processes=(BOT,),
    )
    out = subprocess.run(
        ["sh", "-c", build_status_script((app,))],
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{shim.as_posix()}:{os.environ['PATH']}"},
        check=False,
    ).stdout
    status = parse_status(out, (app,))[0]
    assert status.head_time > 1_700_000_000  # .git/HEAD was just written
    assert status.processes[0].started == 1_578_304_800  # 2020-01-06 10:00 UTC
    assert (
        process_next(app, status, BOT, status.processes[0]).label
        == "Old code (Restart)"
    )


def test_a_unit_in_a_restart_loop_says_so_instead_of_showing_nothing():
    status = live(APP)
    _, server = status.processes
    server.active, server.sub, server.restarts = "activating", "auto-restart", 19
    step = process_next(APP, status, SERVER, server)
    assert step.label == "Restarting (19 restarts): see Logs"
    assert "Logs" in step.why
    server.restarts = 0
    assert process_next(APP, status, SERVER, server).label == "Starting…"
    from raspi.core.stage import next_step

    plain = parse_status(STATUS, (PLAIN,))[0]
    plain.active, plain.restarts = "activating", 4
    assert next_step(PLAIN, plain).label == "Restarting (4 restarts): see Logs"


def test_a_crash_looping_process_can_be_stopped_and_a_pending_one_cannot(window):
    w, _ = window
    key = ("pi", "vocalog", "server")
    w.select_key(key)
    row = w.model.rows[w.model.row_for_key(key)]
    row.pstatus.active, row.pstatus.sub, row.pstatus.restarts = (
        "activating",
        "auto-restart",
        19,
    )
    w._update_buttons()
    assert w.btn_stop.isEnabled() and w.btn_restart.isEnabled()
    assert not w.btn_start.isEnabled()
    w.model.set_pending(key, "stopping")
    w._update_buttons()
    assert not w.btn_stop.isEnabled() and not w.btn_restart.isEnabled()
