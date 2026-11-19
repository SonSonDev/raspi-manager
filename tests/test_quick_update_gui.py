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
    assert calls == [("a", "", False)] and planned == [(host, "plan")]
    assert hasattr(window, "btn_quick_update")
    window.close()
