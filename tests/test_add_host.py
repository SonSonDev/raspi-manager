import os

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog
from typer.testing import CliRunner

from raspi.cli.main import app
from raspi.config.loader import ConfigError, HostsFileMissing, load_hosts
from raspi.config.writer import add_host, remove_host, update_host
from raspi.core.models import Host
from raspi.gui.add_host_dialog import AddHostDialog
from raspi.gui.main_window import MainWindow
from raspi.gui.theme import LIGHT

runner = CliRunner()


@pytest.fixture
def home(monkeypatch, tmp_path):
    folder = tmp_path / "fresh" / ".raspi"  # does not exist yet
    monkeypatch.setenv("RASPI_HOME", str(folder))
    return folder


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_missing_file_is_a_distinct_error(home):
    with pytest.raises(HostsFileMissing, match="Add a Pi"):
        load_hosts()


def test_add_host_creates_folder_and_file(home):
    add_host("raspi", "192.168.1.20", "pi")
    assert load_hosts()[0].address == "192.168.1.20"
    assert yaml.safe_load((home / "hosts.yaml").read_text())["hosts"] == [
        {"name": "raspi", "address": "192.168.1.20", "user": "pi"}
    ]


def test_add_host_keeps_existing_hosts_and_backs_up(home):
    add_host("a", "10.0.0.1", "u")
    add_host("b", "b.local", "u")
    assert [h.name for h in load_hosts()] == ["a", "b"]
    assert (home / "hosts.yaml.bak").exists()


def test_add_host_refuses_duplicate_name(home):
    add_host("a", "10.0.0.1", "u")
    with pytest.raises(ConfigError, match="already exists"):
        add_host("a", "10.0.0.2", "u")


@pytest.mark.parametrize(
    "args",
    [
        ("bad name", "10.0.0.1", "u"),
        ("a", "10.0.0.1; rm -rf /", "u"),
        ("a", "-oProxyCommand=x", "u"),
        ("a", "10.0.0.1", "Root"),
    ],
)
def test_add_host_rejects_unsafe_values_and_writes_nothing(home, args):
    with pytest.raises(ConfigError):
        add_host(*args)
    assert not (home / "hosts.yaml").exists()


def test_cli_host_add(home):
    ok = runner.invoke(app, ["host", "add", "raspi", "raspi.local", "pi"])
    assert ok.exit_code == 0 and "Added raspi" in ok.output
    again = runner.invoke(app, ["host", "add", "raspi", "raspi.local", "pi"])
    assert again.exit_code == 1 and "already exists" in again.output


def test_add_host_from_the_window_hides_the_how_to_panel(qapp, home, monkeypatch):
    w = MainWindow([], object(), LIGHT, auto_refresh=False)
    assert not w.setup_panel.isHidden()
    monkeypatch.setattr(w, "refresh", lambda: None)

    def fake_exec(self):
        self.result_value = ("raspi", "10.0.0.1", "pi")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(AddHostDialog, "exec", fake_exec)
    w.add_host()
    assert w.setup_panel.isHidden() and [r.host.name for r in w.model.rows] == ["raspi"]
    w.close()


def test_dialog_rejects_taken_name_and_bad_address(qapp):
    d = AddHostDialog({"raspi"})
    d.name_edit.setText("raspi")
    d.address_edit.setText("10.0.0.1")
    d.user_edit.setText("pi")
    d._accept()
    assert d.result_value is None and "already exists" in d.error.text()
    d.name_edit.setText("new")
    d.address_edit.setText("bad address")
    d._accept()
    assert d.result_value is None and "address" in d.error.text()
    d.address_edit.setText("10.0.0.2")
    d._accept()
    assert d.result_value == ("new", "10.0.0.2", "pi")


# --- edit / remove -------------------------------------------------------------
APP_YAML = """hosts:
- name: pi
  address: 10.0.0.1
  user: u
  apps:
  - {name: game, unit: game.service}
- name: other
  address: 10.0.0.2
  user: u
"""


@pytest.fixture
def with_apps(monkeypatch, tmp_path):
    monkeypatch.setenv("RASPI_HOME", str(tmp_path))
    (tmp_path / "hosts.yaml").write_text(APP_YAML, encoding="utf-8")
    return tmp_path


def test_update_host_changes_address_and_user_keeps_apps(with_apps):
    update_host("pi", "raspi.local", "pi")
    pi = load_hosts()[0]
    assert (pi.name, pi.address, pi.user) == ("pi", "raspi.local", "pi")
    assert [a.name for a in pi.apps] == ["game"]


def test_update_host_rejects_unsafe_and_unknown(with_apps):
    with pytest.raises(ConfigError):
        update_host("pi", "-oProxyCommand=x", "u")
    with pytest.raises(ConfigError, match="Unknown host"):
        update_host("nope", "10.0.0.9", "u")
    assert load_hosts()[0].address == "10.0.0.1"


def test_remove_host_forgets_it_with_its_apps(with_apps):
    remove_host("pi")
    assert [h.name for h in load_hosts()] == ["other"]
    assert (with_apps / "hosts.yaml.bak").exists()
    remove_host("other")
    assert load_hosts() == []
    with pytest.raises(ConfigError, match="Unknown host"):
        remove_host("other")


def test_cli_host_ls_edit_rm(with_apps):
    ls = runner.invoke(app, ["host", "ls"])
    assert "pi\tu@10.0.0.1\t1 app(s)" in ls.output
    edit = runner.invoke(app, ["host", "edit", "pi", "--address", "raspi.local"])
    assert edit.exit_code == 0 and load_hosts()[0].address == "raspi.local"
    assert runner.invoke(app, ["host", "rm", "pi"], input="n\n").exit_code == 1
    assert [h.name for h in load_hosts()] == ["pi", "other"]
    assert runner.invoke(app, ["host", "rm", "pi", "--yes"]).exit_code == 0
    assert [h.name for h in load_hosts()] == ["other"]
    assert runner.invoke(app, ["host", "rm", "pi", "--yes"]).exit_code == 1


def test_window_edit_and_remove(qapp, with_apps, monkeypatch):
    w = MainWindow(load_hosts(), object(), LIGHT, auto_refresh=False)
    monkeypatch.setattr(w, "refresh", lambda: None)
    w.select_key(("pi", "game"))
    assert w.btn_edit_host.isEnabled() and w.btn_remove_host.isEnabled()

    def fake_exec(self):
        self.result_value = ("pi", "raspi.local", "pi")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(AddHostDialog, "exec", fake_exec)
    w.edit_host()
    assert w._hosts["pi"].address == "raspi.local"

    monkeypatch.setattr(w, "_confirm_remove_host", lambda host: False)
    w.remove_host()
    assert "pi" in w._hosts
    monkeypatch.setattr(w, "_confirm_remove_host", lambda host: True)
    w.remove_host()
    assert list(w._hosts) == ["other"]
    assert w.selected_host() is None and not w.btn_edit_host.isEnabled()
    w.close()


def test_edit_dialog_locks_the_name(qapp):
    host = Host("pi", "10.0.0.1", "u")
    d = AddHostDialog({"pi", "other"}, editing=host)
    assert d.name_edit.isReadOnly() and d.address_edit.text() == "10.0.0.1"
    d.address_edit.setText("raspi.local")
    d._accept()
    assert d.result_value == ("pi", "raspi.local", "u")
