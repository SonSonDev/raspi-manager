import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from raspi.config.loader import load_hosts
from raspi.gui import setup_panel
from raspi.gui.main_window import MainWindow
from raspi.gui.theme import LIGHT

HOSTS_YAML = """hosts:
- name: pi
  address: 10.0.0.1
  user: u
  apps:
  - {name: game, unit: game.service}
"""


@pytest.fixture
def home(monkeypatch, tmp_path):
    folder = tmp_path / "fresh" / ".raspi"
    monkeypatch.setenv("RASPI_HOME", str(folder))
    return folder


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_first_run_window_shows_the_how_to_panel(qapp, home):
    w = MainWindow([], object(), LIGHT, auto_refresh=False)
    assert w.banner.isHidden() and not w.setup_panel.isHidden()
    assert w.setup_panel.guide_button.text() == "Open setup guide"
    w.close()


def test_panel_is_hidden_when_a_pi_exists(qapp, monkeypatch, tmp_path):
    monkeypatch.setenv("RASPI_HOME", str(tmp_path))
    (tmp_path / "hosts.yaml").write_text(HOSTS_YAML, encoding="utf-8")
    w = MainWindow(load_hosts(), object(), LIGHT, auto_refresh=False)
    assert w.banner.isHidden() and w.setup_panel.isHidden()
    w.close()


def test_empty_hosts_file_means_no_pi_yet(home):
    home.mkdir(parents=True)
    (home / "hosts.yaml").write_text("", encoding="utf-8")
    assert load_hosts() == []


def test_open_guide_reports_a_missing_file(qapp, monkeypatch, tmp_path):
    shown = []
    monkeypatch.setattr(setup_panel, "GUIDE", tmp_path / "nope.md")
    monkeypatch.setattr(
        setup_panel.QMessageBox, "information", lambda *a: shown.append(a[2])
    )
    assert setup_panel.open_guide() is False and "not found" in shown[0]


def test_guide_exists_in_the_checkout():
    assert setup_panel.GUIDE.is_file()
