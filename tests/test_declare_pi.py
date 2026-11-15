import base64
import os
import re

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from raspi.config.loader import load_hosts, parse_manifest
from raspi.core.manifest import render_manifest
from raspi.core.models import AppRef, EnvFile, Host
from raspi.gui.declare import run_declaration
from raspi.services.declare import DeclareResult, declare_env_file, forget_env_file
from raspi.ssh.transport import RunResult

HOST = Host("pi", "10.0.0.1", "u")
GAME = AppRef(
    "game",
    "game.service",
    "/home/u/game",
    (EnvFile("server", "/home/u/game/server/.env"),),
)
BOT = AppRef("bot", "bot.service", "/home/u/bot")


class Pi:
    """A Pi holding apps.yaml: answers reads, applies base64 writes, remembers scripts."""

    def __init__(self, apps: tuple[AppRef, ...] | None):
        self.text = None if apps is None else render_manifest(apps)
        self.scripts, self.backups = [], 0

    def run_script(self, host, script, timeout=20.0):
        self.scripts.append(script)
        if "base64 -d" in script:
            if self.text is not None and 'cp -p "$target" "$target.bak"' in script:
                self.backups += 1
            self.text = base64.b64decode(
                re.search(r"printf %s '?([A-Za-z0-9+/=]+)'? \|", script)[1]
            ).decode()
            return RunResult(True, "written\n")
        if self.text is None:
            return RunResult(True, "@@MISSING\n")
        return RunResult(True, "@@EXISTS\n" + self.text)

    def apps(self):
        return parse_manifest(yaml.safe_load(self.text))


def test_several_env_files_can_be_declared_on_the_pi():
    pi = Pi((GAME, BOT))
    result = declare_env_file(
        HOST, "game", "client", "/home/u/game/.env", pi, on_pi=True
    )
    assert result.ok and "apps.yaml.bak" in result.message
    assert [(f.label, f.path) for f in result.app.env_files] == [
        ("server", "/home/u/game/server/.env"),
        ("client", "/home/u/game/.env"),
    ]
    game, bot = pi.apps()
    assert game == result.app and bot == BOT  # the other app is untouched
    assert pi.backups == 1


def test_first_env_file_of_an_app_and_text_stays_free_of_values():
    pi = Pi((GAME, BOT))
    result = declare_env_file(
        HOST, "bot", "default", "/home/u/bot/.env", pi, on_pi=True
    )
    assert result.ok and pi.apps()[1].env_files == (
        EnvFile("default", "/home/u/bot/.env"),
    )
    assert "=" not in pi.text.split("version")[0]  # header comments hold no values


@pytest.mark.parametrize(
    "label, path, expected",
    [
        ("server", "/home/u/x/.env", "already has a file labelled"),
        ("other", "/home/u/game/server/.env", "already declared"),
        ("bad label", "/home/u/x/.env", "label"),
        ("ok", "/etc/app/.env", "user-writable"),
        ("ok", "relative/.env", "Invalid env_file"),
    ],
)
def test_invalid_declarations_change_nothing_on_the_pi(label, path, expected):
    pi = Pi((GAME,))
    before = pi.text
    result = declare_env_file(HOST, "game", label, path, pi, on_pi=True)
    assert not result.ok and expected in result.message
    assert pi.text == before and pi.backups == 0


def test_forget_one_of_several_files_keeps_the_others():
    both = AppRef(
        "game",
        "game.service",
        None,
        (EnvFile("server", "/home/u/s/.env"), EnvFile("client", "/home/u/c/.env")),
    )
    pi = Pi((both,))
    result = forget_env_file(HOST, "game", "server", pi, on_pi=True)
    assert result.ok and [f.label for f in result.app.env_files] == ["client"]
    assert [f.label for f in pi.apps()[0].env_files] == ["client"]
    last = forget_env_file(HOST, "game", "client", pi, on_pi=True)
    assert last.ok and last.app.env_files == () and pi.apps()[0].env_files == ()


def test_change_is_applied_on_top_of_the_file_read_now():
    pi = Pi((GAME, BOT))
    pi.text = render_manifest(
        (GAME, BOT, AppRef("new", "new.service"))
    )  # edited meanwhile
    declare_env_file(HOST, "game", "client", "/home/u/game/.env", pi, on_pi=True)
    assert [a.name for a in pi.apps()] == ["game", "bot", "new"]


def test_pi_failures_are_reported_not_raised():
    assert (
        "no apps.yaml"
        in declare_env_file(
            HOST, "game", "a", "/home/u/a/.env", Pi(None), on_pi=True
        ).message
    )
    assert (
        "not in apps.yaml"
        in declare_env_file(
            HOST, "ghost", "a", "/home/u/a/.env", Pi((GAME,)), on_pi=True
        ).message
    )
    broken = Pi((GAME,))
    broken.text = "version: 9\n"
    assert not declare_env_file(
        HOST, "game", "a", "/home/u/a/.env", broken, on_pi=True
    ).ok

    class Down:
        def run_script(self, host, script, timeout=20.0):
            return RunResult(False, "", "timeout")

    assert (
        "Cannot read" in forget_env_file(HOST, "game", "server", Down(), True).message
    )


def test_pcs_without_an_inventory_still_use_hosts_yaml(monkeypatch, tmp_path):
    monkeypatch.setenv("RASPI_HOME", str(tmp_path))
    (tmp_path / "hosts.yaml").write_text(
        "hosts:\n- {name: pi, address: 10.0.0.1, user: u, apps: [{name: game, unit: game.service}]}\n"
    )
    result = declare_env_file(
        HOST, "game", "server", "/home/u/game/.env", Pi(None), on_pi=False
    )
    assert result.ok and "hosts.yaml" in result.message
    assert load_hosts()[0].apps[0].env_files == (
        EnvFile("server", "/home/u/game/.env"),
    )
    gone = forget_env_file(HOST, "game", "server", Pi(None), on_pi=False)
    assert gone.ok and gone.app.env_files == ()


def test_run_declaration_waits_for_the_worker():
    QApplication.instance() or QApplication([])
    ok = run_declaration(lambda: DeclareResult(True, "fine"))
    assert ok.ok and ok.message == "fine"

    def boom():
        raise RuntimeError("worker died")

    failed = run_declaration(boom)
    assert not failed.ok and "worker died" in failed.message
