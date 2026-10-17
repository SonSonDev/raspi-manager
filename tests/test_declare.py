import os
import time

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from raspi.config.loader import ConfigError, load_hosts
from raspi.config.writer import add_env_file, remove_env_file
from raspi.core.models import AppRef, Host
from raspi.core.naming import suggest_label
from raspi.core.remote import build_env_detect_script, parse_env_detect
from raspi.gui.add_env_dialog import AddEnvFileDialog
from raspi.services.env import detect_env_files
from raspi.ssh.transport import RunResult

YAML = """hosts:
- name: pi
  address: 10.0.0.1
  user: u
  apps:
  - {name: game, unit: game.service, path: /home/u/game, env_file: /home/u/game/server/.env}
  - {name: bot, unit: bot.service, path: /home/u/bot}
- name: other
  address: 10.0.0.2
  user: u
"""


@pytest.fixture
def hosts_file(monkeypatch, tmp_path):
    monkeypatch.setenv("RASPI_HOME", str(tmp_path))
    path = tmp_path / "hosts.yaml"
    path.write_text(YAML, encoding="utf-8")
    return path


# --- hosts.yaml writer -----------------------------------------------------------
def test_add_to_app_without_files(hosts_file):
    add_env_file("pi", "bot", "default", "/home/u/bot/.env")
    bot = next(a for a in load_hosts()[0].apps if a.name == "bot")
    assert [(e.label, e.path) for e in bot.env_files] == [
        ("default", "/home/u/bot/.env")
    ]


def test_add_converts_single_env_file_shortcut_and_keeps_the_rest(hosts_file):
    add_env_file("pi", "game", "client", "/home/u/game/.env")
    game = load_hosts()[0].apps[0]
    assert [(e.label, e.path) for e in game.env_files] == [
        ("default", "/home/u/game/server/.env"),
        ("client", "/home/u/game/.env"),
    ]
    raw = yaml.safe_load(hosts_file.read_text(encoding="utf-8"))
    assert "env_file" not in raw["hosts"][0]["apps"][0]
    assert (
        raw["hosts"][1]["name"] == "other"
        and raw["hosts"][0]["apps"][1]["name"] == "bot"
    )
    assert (hosts_file.parent / "hosts.yaml.bak").read_text(encoding="utf-8") == YAML


def test_invalid_input_never_touches_the_file(hosts_file):
    before = hosts_file.read_bytes()
    for label, path in (
        ("x", "/etc/passwd"),
        ("x", "rel/.env"),
        ("a b", "/home/u/x"),
        ("", "/home/u/x"),
    ):
        with pytest.raises(ConfigError):
            add_env_file("pi", "bot", label, path)
    with pytest.raises(ConfigError):
        add_env_file("pi", "ghost", "x", "/home/u/x")
    with pytest.raises(ConfigError):
        add_env_file("nope", "bot", "x", "/home/u/x")
    assert hosts_file.read_bytes() == before
    assert not (hosts_file.parent / "hosts.yaml.bak").exists()


def test_duplicates_rejected(hosts_file):
    with pytest.raises(ConfigError):
        add_env_file("pi", "game", "default", "/home/u/other")
    with pytest.raises(ConfigError):
        add_env_file("pi", "game", "again", "/home/u/game/server/.env")


def test_remove_forgets_declaration_only(hosts_file):
    add_env_file("pi", "game", "client", "/home/u/game/.env")
    remove_env_file("pi", "game", "default")
    assert [e.label for e in load_hosts()[0].apps[0].env_files] == ["client"]
    remove_env_file("pi", "game", "client")
    assert load_hosts()[0].apps[0].env_files == ()


# --- detection -------------------------------------------------------------------
def test_detect_script_is_read_only_and_parse_filters_templates():
    script = build_env_detect_script("/home/u/game")
    assert "find . -maxdepth 2" in script and "-name node_modules" in script
    for forbidden in ("sudo", "rm ", ">>", " > ", "mv ", "cp "):
        assert forbidden not in script
    out = "./.env\n./server/.env\n./server/.env.example\n./.env.raspi-bak\n./client/.env.local\n"
    assert parse_env_detect("/home/u/game", out) == [
        "/home/u/game/.env",
        "/home/u/game/client/.env.local",
        "/home/u/game/server/.env",
    ]
    assert parse_env_detect("/home/u/game", "@@NODIR\n") is None


def test_suggest_label():
    assert suggest_label("/a/game", "/a/game/server/.env", set()) == "server"
    assert suggest_label("/a/game", "/a/game/.env", set()) == "default"
    assert suggest_label("/a/game", "/a/game/.env", {"default"}) == "root"
    assert suggest_label("/a/game", "/a/game/server/.env", {"server"}) == "server-2"
    assert suggest_label(None, "/x/y/.env", set()) == "default"


# --- dialog ------------------------------------------------------------------------
@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class Transport:
    def __init__(self, stdout="./.env\n./server/.env\n", ok=True, error=""):
        self.scripts, self._r = [], RunResult(ok, stdout, error)

    def run_script(self, host, script, timeout=20.0):
        self.scripts.append(script)
        return self._r


def pump(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        QCoreApplication.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


APP = AppRef("game", "game.service", "/home/u/game")
HOST = Host("pi", "10.0.0.1", "u", (APP,))


def test_dialog_lists_candidates_and_suggests_a_label(qapp):
    t = Transport()
    d = AddEnvFileDialog(HOST, APP, t, set(), {"/home/u/game/.env"})
    assert pump(lambda: d.path_box.count() > 0)
    assert [d.path_box.itemText(i) for i in range(d.path_box.count())] == [
        "/home/u/game/server/.env"
    ]
    assert d.label_edit.text() == "server"
    assert len(t.scripts) == 1 and "find" in t.scripts[0]
    d._accept()
    assert d.result_value == ("server", "/home/u/game/server/.env")


def test_dialog_validates_input(qapp):
    d = AddEnvFileDialog(HOST, APP, Transport("@@NODIR\n"), {"server"}, set())
    assert pump(lambda: "does not exist" in d.hint.text())
    d.path_box.setEditText("/etc/passwd")
    d.label_edit.setText("x")
    d._accept()
    assert d.result_value is None and not d.error.isHidden()
    d.path_box.setEditText("/home/u/game/.env")
    d.label_edit.setText("server")  # label already used
    d._accept()
    assert d.result_value is None
    d.label_edit.setText("client")
    d._accept()
    assert d.result_value == ("client", "/home/u/game/.env")


def test_detect_error_is_reported_not_raised(qapp):
    with pytest.raises(RuntimeError):
        detect_env_files(HOST, "/home/u/game", Transport(ok=False, error="down"))
    d = AddEnvFileDialog(HOST, APP, Transport(ok=False, error="down"), set(), set())
    assert pump(lambda: "Could not look" in d.hint.text())
