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
