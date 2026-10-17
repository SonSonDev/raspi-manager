"""Entry point of the native (Qt) app: `raspi gui`."""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from raspi.config.loader import ConfigError, load_hosts
from raspi.gui.main_window import MainWindow
from raspi.gui.theme import DARK, LIGHT, stylesheet
from raspi.ssh.transport import SshTransport


def _tokens(app: QApplication):
    return DARK if app.styleHints().colorScheme() == Qt.ColorScheme.Dark else LIGHT


def run() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setApplicationName("raspi")
    try:
        hosts, error = load_hosts(), ""
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
    window.show()
    return app.exec()
