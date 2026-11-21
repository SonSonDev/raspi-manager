"""Entry point of the native (Qt) app: `raspi gui`."""

from __future__ import annotations

import sys

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QStyleFactory

from raspi import APP_NAME
from raspi.config.loader import ConfigError, HostsFileMissing, load_hosts
from raspi.gui.icon import app_icon, set_windows_identity
from raspi.gui.main_window import MainWindow
from raspi.gui.theme import DARK, LIGHT, stylesheet
from raspi.ssh.transport import SshTransport


def _tokens(app: QApplication):
    return DARK if app.styleHints().colorScheme() == Qt.ColorScheme.Dark else LIGHT


def run() -> int:
    set_windows_identity()  # before any window: the taskbar then shows our icon
    app = QApplication.instance() or QApplication(sys.argv)
    app.setWindowIcon(app_icon())
    # the platform's own look (Windows 11 style, system colours, light/dark and fonts)
    styles = QStyleFactory.keys()
    app.setStyle("windows11" if "windows11" in styles else "Fusion")
    app.setApplicationName(APP_NAME)
    try:
        hosts, error = load_hosts(), ""
    except HostsFileMissing:  # first run: the window explains how to add a Pi
        hosts, error = [], ""
    except ConfigError as exc:
        hosts, error = [], str(exc)

    tokens = _tokens(app)
    app.setStyleSheet(stylesheet(tokens))
    window = MainWindow(hosts, SshTransport(), tokens, error)

    def retheme(*_: object) -> None:
        t = _tokens(app)
        app.setStyleSheet(stylesheet(t))
        window.model.set_tokens(t)

    app.styleHints().colorSchemeChanged.connect(retheme)
    # remembered between runs; read here, not in the window, so tests never touch the registry
    settings = QSettings(APP_NAME, APP_NAME)
    window.btn_on_top.setChecked(settings.value("keep_on_top", False, type=bool))
    window.btn_on_top.toggled.connect(lambda on: settings.setValue("keep_on_top", on))
    window.show()
    return app.exec()
