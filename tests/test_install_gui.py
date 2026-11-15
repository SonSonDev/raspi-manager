import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from raspi.config.loader import ConfigError
from raspi.core.models import AppRef, Host
from raspi.gui.install_dialog import InstallAppDialog, app_from_fields

HOST = Host("pi", "10.0.0.1", "pi", (AppRef("old", "old.service"),))


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_fields_become_a_validated_app():
    app = app_from_fields(
        "notes-app", "SonSonDev/notes-app", "npm ci", "npm start", ""
    )
    assert (app.unit, app.repo, app.build, app.run, app.path) == (
        "notes-app.service",
        "SonSonDev/notes-app",
        "npm ci",
        "npm start",
        None,
    )
    for bad in (("a b", "o/r"), ("a", "nope")):
        with pytest.raises(ConfigError):
            app_from_fields(bad[0], bad[1], "", "", "")


def test_dialog_accepts_valid_input_and_refuses_a_taken_name(qapp):
    dialog = InstallAppDialog(HOST)
    dialog.name_edit.setText("old")
    dialog.repo_edit.setText("me/old")
    dialog.run_edit.setText("npm start")
    dialog._accept()
    assert dialog.result_value is None and "already" in dialog.error.text()
    dialog.name_edit.setText("new")
    dialog._accept()
    assert dialog.result_value and dialog.result_value.name == "new"


def test_window_has_the_install_actions(qapp):
    from raspi.gui.main_window import MainWindow

    assert hasattr(MainWindow, "install_app_form") and hasattr(
        MainWindow, "install_unit_form"
    )


def test_no_method_name_is_defined_twice_across_the_window_mixins(qapp):
    """A mixin method shadowed by MainWindow's own (same name, other signature) fails silently."""
    from collections import defaultdict

    from raspi.gui.main_window import MainWindow

    seen = defaultdict(list)
    for cls in MainWindow.__mro__:
        if cls.__module__.startswith("raspi"):
            for name, value in vars(cls).items():
                if callable(value) and not name.startswith("__"):
                    seen[name].append(cls.__name__)
    assert {n: c for n, c in seen.items() if len(c) > 1} == {}


def test_install_flow_runs_the_script_after_confirmation(qapp, monkeypatch):
    from raspi.gui import install_actions
    from raspi.gui.main_window import MainWindow
    from raspi.gui.theme import LIGHT
    from raspi.ssh.transport import RunResult

    class Pi:
        def __init__(self):
            self.scripts = []

        def run_script(self, host, script, timeout=20.0):
            self.scripts.append(script)
            if "state=" in script:
                return RunResult(True, "key=yes\nstate=missing\ndisk_mb=9000\n")
            if "git rev-parse" in script:
                return RunResult(True, "rev=abc1234\n")
            return RunResult(True, "@@MISSING\n")

    def inline(fn, on_done, on_failed=None):
        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001
            on_failed(str(exc))
            return
        on_done(result)

    shown = []
    monkeypatch.setattr(install_actions, "submit", inline)
    monkeypatch.setattr(
        install_actions.QMessageBox, "information", lambda *a: shown.append(a[2])
    )
    monkeypatch.setattr(
        install_actions.QMessageBox, "critical", lambda *a: shown.append(a[2])
    )
    pi = Pi()
    window = MainWindow([HOST], pi, LIGHT, auto_refresh=False)
    monkeypatch.setattr(MainWindow, "_confirm_change", lambda *a: True)
    app = app_from_fields("notes-app", "me/notes-app", "npm ci", "npm start", "")
    from raspi.services.install import plan_install

    window._on_install_planned((HOST, plan_install(HOST, pi, app)))
    assert any("git clone" in s for s in pi.scripts)
    assert shown and "built on pi" in shown[0]
    window.close()


def test_run_command_is_required_and_an_existing_app_is_prefilled(qapp):
    dialog = InstallAppDialog(HOST)
    dialog.name_edit.setText("new")
    dialog.repo_edit.setText("me/new")
    dialog._accept()
    assert dialog.result_value is None and "run command" in dialog.error.text()
    app = AppRef("old", "old.service", repo="me/old", run="npm start")
    update = InstallAppDialog(HOST, existing=app)
    assert update.name_edit.isReadOnly() and update.run_edit.text() == "npm start"
    update.build_edit.setText("npm ci")
    update._accept()
    assert update.result_value.build == "npm ci" and update.result_value.name == "old"
