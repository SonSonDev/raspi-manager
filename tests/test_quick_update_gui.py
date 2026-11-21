import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from raspi.core.models import AppRef, Host


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_quick_update_plans_the_latest_without_a_dialog(qapp, monkeypatch):
    from raspi.gui import update_actions
    from raspi.gui.main_window import MainWindow
    from raspi.gui.theme import LIGHT

    host = Host("pi", "10.0.0.1", "pi", (AppRef("a", "a.service", repo="me/a"),))
    calls = []
    monkeypatch.setattr(
        update_actions,
        "plan_update",
        lambda h, t, app, ref, rollback: (
            calls.append((app.name, ref, rollback)) or "plan"
        ),
    )
    planned = []
    monkeypatch.setattr(
        update_actions, "submit", lambda fn, ok, bad=None: planned.append(fn())
    )
    window = MainWindow([host], object(), LIGHT, auto_refresh=False)
    row = type("Row", (), {"host": host, "app": host.apps[0]})()
    monkeypatch.setattr(MainWindow, "selected", lambda self: row)
    window.quick_update()
    assert calls == [("a", "", False)] and planned == [(host, "plan", True)]
    assert hasattr(window, "btn_quick_update")
    window.close()


def test_keep_on_top_toggles_the_window_flag(qapp):
    from PySide6.QtCore import Qt

    from raspi.gui.main_window import MainWindow
    from raspi.gui.theme import LIGHT

    window = MainWindow([], object(), LIGHT, auto_refresh=False)
    window.show()
    flag = Qt.WindowType.WindowStaysOnTopHint
    assert not window.windowFlags() & flag
    window.btn_on_top.trigger()
    assert window.windowFlags() & flag and window.isVisible()
    window.btn_on_top.trigger()
    assert not window.windowFlags() & flag and not window.btn_on_top.isChecked()
    window.close()


def test_update_now_applies_without_asking(qapp, monkeypatch):
    from raspi.gui import update_actions
    from raspi.gui.main_window import MainWindow
    from raspi.gui.theme import LIGHT

    host = Host("pi", "10.0.0.1", "pi", (AppRef("a", "a.service", repo="me/a"),))
    plan = type(
        "Plan",
        (),
        {
            "nothing_to_do": False,
            "commits": [],
            "script": "s",
            "rollback": False,
            "new": "abcdef0123",
            "app": host.apps[0],
            "steps": [],
            "warnings": [],
            "processes": [],
        },
    )()
    applied = []
    monkeypatch.setattr(update_actions, "plan_update", lambda *a: plan)
    monkeypatch.setattr(
        update_actions, "apply_update", lambda h, t, p: applied.append(p) or "done"
    )
    monkeypatch.setattr(update_actions, "submit", lambda fn, ok, bad=None: ok(fn()))
    window = MainWindow([host], object(), LIGHT, auto_refresh=False)
    asked = []
    monkeypatch.setattr(
        MainWindow, "_confirm_update", lambda *a: asked.append(1) or True
    )
    monkeypatch.setattr(MainWindow, "_on_updated", lambda self, payload: None)
    row = type("Row", (), {"host": host, "app": host.apps[0]})()
    monkeypatch.setattr(MainWindow, "selected", lambda self: row)
    window.update_now()
    assert applied == [plan] and not asked
    window.close()
