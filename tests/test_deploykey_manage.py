import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from typer.testing import CliRunner

from raspi.core.deploykey import DeployKeyError
from raspi.core.deploykey_manage import (
    build_delete_script,
    build_list_script,
    build_rotate_script,
    parse_list,
)
from raspi.core.models import AppRef, Host
from raspi.core.remove import KEY_MARKER
from raspi.services.deploykey import delete_key, list_keys, rotate_key
from raspi.ssh.transport import RunResult

KEYS = ["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMq"]
APPS = (AppRef("game", "game.service", repo="me/game"),)
HOST = Host("pi", "10.0.0.1", "pi", APPS)
PUB = "ssh-ed25519 AAAAC3Nza raspi-game@pi"
LISTING = (
    f"key|game|SHA256:abc|{PUB}\n"
    f"key|old|SHA256:def|ssh-ed25519 AAAAB raspi-old@pi\n"
    "block|game\nblock|ghost\nblock|bad name;id\n"
)


class Pi:
    def __init__(self, stdout="", ok=True):
        self.stdout, self.ok, self.scripts = stdout, ok, []

    def run_script(self, host, script, timeout=20.0):
        self.scripts.append(script)
        return RunResult(self.ok, self.stdout, "" if self.ok else "boom")


def test_parse_list_merges_files_and_blocks_and_skips_foreign_names():
    infos = {i.name: i for i in parse_list(LISTING)}
    assert set(infos) == {"game", "old", "ghost"}
    assert infos["game"].complete and infos["game"].public_key == PUB
    assert infos["old"].has_files and not infos["old"].has_block
    assert infos["ghost"].has_block and not infos["ghost"].has_files


def test_list_script_is_read_only():
    script = build_list_script()
    assert KEY_MARKER in script
    for word in ("rm ", "ssh-keygen -q", ">>", "mv "):
        assert word not in script


def test_rotate_script_removes_the_old_key_before_making_a_new_one():
    script = build_rotate_script("game", "pi", KEYS)
    assert script.index("rm -f") < script.index("ssh-keygen -q")
    assert "Host github-game" in script and script.count("set -e") == 2


def test_delete_script_only_touches_that_app():
    script = build_delete_script("game")
    assert "raspi_deploy_game" in script and "github-game" in script
    assert script.rstrip().endswith('echo "removed"')
    with pytest.raises(DeployKeyError):
        build_delete_script("game; id")


def test_service_functions():
    assert [k.name for k in list_keys(HOST, Pi(LISTING)).keys] == [
        "game",
        "ghost",
        "old",
    ]
    assert not list_keys(HOST, Pi(ok=False)).ok
    pi = Pi()
    assert rotate_key(HOST, pi, "game", dry_run=True, lookup=lambda: KEYS).script
    assert delete_key(HOST, pi, "game", dry_run=True).ok and not pi.scripts
    rotated = rotate_key(HOST, Pi(f"pub={PUB}\n"), "game", lookup=lambda: KEYS)
    assert rotated.ok and rotated.public_key == PUB
    assert not rotate_key(HOST, Pi("nothing"), "game", lookup=lambda: KEYS).ok
    assert delete_key(HOST, Pi("removed"), "game").ok
    assert not delete_key(HOST, Pi(ok=False), "game").ok


def test_cli_list_check_rotate_and_delete(monkeypatch):
    from raspi.cli import main as cli

    monkeypatch.setattr(cli, "_live_host", lambda name: (HOST, HOST))
    pi = Pi(LISTING)
    monkeypatch.setattr("raspi.cli.deploykeys.SshTransport", lambda: pi)
    monkeypatch.setattr("raspi.cli.deploykeys.fetch_github_keys", lambda: KEYS)
    run = CliRunner().invoke
    listed = run(cli.app, ["host", "deploy-keys", "pi"])
    assert listed.exit_code == 0
    assert "used by me/game" in listed.output and "no app uses it" in listed.output
    assert "incomplete" in listed.output
    rotate = run(cli.app, ["host", "deploy-key-rotate", "pi", "game", "--dry-run"])
    assert rotate.exit_code == 0 and "ssh-keygen" in rotate.output
    gone = run(cli.app, ["host", "deploy-key-rm", "pi", "game", "--yes"])
    assert gone.exit_code == 0 and "deleted" in gone.output


def test_dialog_lists_checks_and_selects():
    from PySide6.QtWidgets import QApplication

    from raspi.gui.deploykeys_dialog import DeployKeysDialog

    QApplication.instance() or QApplication([])
    pi = Pi(LISTING)
    dialog = DeployKeysDialog(HOST, APPS, pi, parent=None)
    for _ in range(100):
        QApplication.processEvents()
        if dialog.table.rowCount():
            break
        import time

        time.sleep(0.02)
    assert dialog.table.rowCount() == 3
    assert not dialog.btn_rotate.isEnabled()
    dialog.table.selectRow(0)  # game: key and alias
    assert dialog.btn_rotate.isEnabled() and dialog.btn_copy.isEnabled()
    dialog.table.selectRow(1)  # ghost: alias only, no key file
    assert not dialog.btn_rotate.isEnabled() and dialog.btn_delete.isEnabled()
