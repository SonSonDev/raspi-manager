import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication
from test_move import DST, SRC, Pis

from raspi.gui import busy, move_actions
from raspi.services.move import plan_move


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def inline(fn, on_done, on_failed=None):
    try:
        result = fn()
    except Exception as exc:  # noqa: BLE001
        on_failed(str(exc))
        return
    on_done(result)


@pytest.fixture
def window(qapp, monkeypatch):
    from raspi.gui.main_window import MainWindow
    from raspi.gui.theme import LIGHT

    shown = []
    monkeypatch.setattr(move_actions, "submit", inline)
    monkeypatch.setattr(
        move_actions.QMessageBox, "information", lambda *a: shown.append(("ok", a[2]))
    )
    monkeypatch.setattr(
        move_actions.QMessageBox, "critical", lambda *a: shown.append(("bad", a[2]))
    )
    pis = Pis()
    win = MainWindow([SRC, DST], pis, LIGHT, auto_refresh=False)
    monkeypatch.setattr(MainWindow, "_refresh_host", lambda self, name: None)
    win.pis, win.shown = pis, shown
    yield win
    win.close()
    busy.end("old")
    busy.end("new")


def test_menu_item_is_off_until_an_app_is_selected(window):
    assert not window.btn_move.isEnabled()


def test_confirmed_move_runs_and_reports(window, monkeypatch):
    monkeypatch.setattr(move_actions, "confirm_change", lambda *a: True)
    window._on_move_planned(plan_move(SRC, DST, "journal", window.pis))
    assert window.pis.log[-1] == "disable old" and window.shown[0][0] == "ok"
    assert "runs on new" in window.shown[0][1] and "hunter2" not in window.shown[0][1]
    assert not busy.running("old") and not busy.running("new")


def test_cancelled_move_changes_nothing(window, monkeypatch):
    monkeypatch.setattr(move_actions, "confirm_change", lambda *a: False)
    window._on_move_planned(plan_move(SRC, DST, "journal", window.pis))
    assert window.pis.log == [] and "Cancelled" in window.status_label.text()


def test_failed_move_is_reported_and_the_source_restarted(window, monkeypatch):
    monkeypatch.setattr(move_actions, "confirm_change", lambda *a: True)
    window.pis.fail_unpack = True
    window._on_move_planned(plan_move(SRC, DST, "journal", window.pis))
    assert window.shown[0][0] == "bad" and "restarted on old" in window.shown[0][1]
    assert window.pis.state["old"] == "active" and not busy.running("old")


def test_move_waits_when_a_change_is_running(window, monkeypatch):
    monkeypatch.setattr(move_actions, "confirm_change", lambda *a: True)
    monkeypatch.setattr(
        move_actions.QMessageBox, "information", lambda *a: window.shown.append(a[2])
    )
    plan = plan_move(SRC, DST, "journal", window.pis)
    busy.begin("new")
    window._on_move_planned(plan)
    assert window.pis.log == [] and not busy.running("old")


def test_refusal_is_shown_as_a_warning(window, monkeypatch):
    seen = []
    monkeypatch.setattr(
        move_actions.QMessageBox, "warning", lambda *a: seen.append(a[2])
    )
    window._move_refused("• already running")
    assert seen == ["• already running"] and "refused" in window.status_label.text()
