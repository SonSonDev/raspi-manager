import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from raspi.core.models import AppRef, Host
from raspi.gui.main_window import MainWindow
from raspi.gui.theme import LIGHT
from raspi.ssh.transport import RunResult

STATUS = """@@APP game
ActiveState=active
SubState=running
UnitFileState=enabled
MainPID=1
NRestarts=0
rev=abc1234
dirty=0
@@APP bot
ActiveState=inactive
SubState=dead
UnitFileState=enabled
MainPID=0
NRestarts=0
"""
HOSTS = [
    Host(
        "pi",
        "10.0.0.1",
        "u",
        (AppRef("game", "game.service", "/g"), AppRef("bot", "bot.service")),
    ),
    Host("empty", "10.0.0.2", "u"),
    Host("down", "10.0.0.3", "u", (AppRef("x", "x.service"),)),
]


class FakeTransport:
    def __init__(self):
        self.scripts = []

    def run_script(self, host, script, timeout=20.0):
        self.scripts.append((host.name, script))
        if host.name == "down":
            return RunResult(False, "", "Permission denied")
        if "sudo -n systemctl" in script:
            return RunResult(True, "exit=0\nActiveState=active\nSubState=running\n")
        if "journalctl" in script:
            return RunResult(True, "log line 1\nlog line 2")
        return RunResult(True, STATUS)


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
def window(qapp):
    transport = FakeTransport()
    w = MainWindow(HOSTS, transport, LIGHT, auto_refresh=False)
    w.show()
    w.refresh()
    assert pump(lambda: not w._inflight)
    yield w, transport
    w.close()


def states(w):
    return [w.model.state_at(i) for i in range(len(w.model.rows))]


def test_states_per_row_after_refresh(window):
    w, _ = window
    assert states(w) == ["running", "stopped", "unknown", "unreachable"]


def test_buttons_follow_selection(window):
    w, _ = window
    w.select_key(("pi", "game"))
    assert (
        w.btn_restart.isEnabled()
        and w.btn_stop.isEnabled()
        and not w.btn_start.isEnabled()
    )
    w.select_key(("pi", "bot"))
    assert w.btn_start.isEnabled() and not w.btn_restart.isEnabled()


def test_restart_needs_confirmation(window, monkeypatch):
    w, transport = window
    w.select_key(("pi", "game"))
    monkeypatch.setattr(w, "_confirm", lambda row, action: False)
    w.act("restart")
    assert not any("systemctl restart" in s for _, s in transport.scripts)
    monkeypatch.setattr(w, "_confirm", lambda row, action: True)
    w.act("restart")
    assert w.model.state_at(w.model.row_for_key(("pi", "game"))) == "working"
    assert pump(lambda: any("systemctl restart" in s for _, s in transport.scripts))
    assert pump(lambda: not w._inflight and w.model.state_at(0) == "running")


def test_selection_survives_refresh_and_logs_panel(window):
    w, _ = window
    w.select_key(("pi", "bot"))
    w.show_logs()
    w.refresh()
    assert pump(lambda: not w._inflight)
    QCoreApplication.processEvents()
    assert w.selected() is not None and w.selected().key == ("pi", "bot")


def test_logs_panel_fills_off_thread(window):
    w, _ = window
    w.select_key(("pi", "game"))
    w.show_logs()
    assert pump(lambda: "log line 2" in w.logs_view.toPlainText())


def test_unreachable_host_does_not_block_others(window):
    w, _ = window
    assert "Permission denied" in w.model.rows[3].host_status.error


def test_screenshot_for_review(window, tmp_path):
    w, _ = window
    w.select_key(("pi", "game"))
    out = os.environ.get("RASPI_SHOT")
    if out:
        w.grab().save(out)
    assert out is None or os.path.exists(out)
