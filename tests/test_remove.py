import base64
import os
import re
import shutil
import subprocess

import pytest
from PySide6.QtWidgets import QLabel

from raspi.config import envstore
from raspi.core.models import AppRef, EnvFile, Host, Requirement
from raspi.core.remove import (
    ENV,
    INVENTORY,
    KEY,
    KEY_MARKER,
    LOCAL,
    UNIT,
    WORKDIR,
    RemoveFacts,
    available_items,
    build_probe_script,
    build_remove_script,
    parse_probe,
)
from raspi.services.remove import RemoveError, apply_remove, plan_remove
from raspi.ssh.transport import RunResult

SH = shutil.which("sh")
ENVFILE = EnvFile("default", "/home/pi/apps/game/.env")
APP = AppRef(
    "game",
    "game.service",
    repo="me/game",
    env_files=(ENVFILE,),
    requires=(Requirement("mount", "/mnt/data"),),
)
FACTS = RemoveFacts(
    home="/home/pi",
    has_sudo=True,
    unit="ours",
    workdir="repo",
    key_files=True,
    key_block=True,
    env_present=(True,),
)
HOST = Host("pi", "10.0.0.1", "pi", (APP,))


def by_key(items):
    return {i.key: i for i in items}


# --- what is offered ---------------------------------------------------------------------
def test_only_the_inventory_entry_and_the_unit_are_ticked_by_default():
    items = by_key(available_items(APP, FACTS, True, True))
    assert [k for k, i in items.items() if i.ticked] == [INVENTORY, UNIT]
    assert all(i.available for i in items.values())


def test_a_foreign_unit_a_missing_thing_and_a_hosts_yaml_app_are_not_offered():
    facts = RemoveFacts(**{**FACTS.__dict__, "unit": "foreign", "workdir": "missing"})
    items = by_key(available_items(APP, facts, False, False))
    assert (
        not items[UNIT].available and "not created by raspi-manager" in items[UNIT].note
    )
    assert not items[WORKDIR].available and not items[LOCAL].available
    assert not items[INVENTORY].available and "hosts.yaml" in items[INVENTORY].note


def test_the_working_copy_is_left_alone_when_it_holds_data_or_is_not_git_or_outside_home():
    holding = AppRef(
        "game",
        "game.service",
        requires=(Requirement("folder", "/home/pi/apps/game/db"),),
    )
    item = by_key(available_items(holding, FACTS, True, False))[WORKDIR]
    assert not item.available and "prerequisite" in item.note
    other = RemoveFacts(**{**FACTS.__dict__, "workdir": "other"})
    assert not by_key(available_items(APP, other, True, False))[WORKDIR].available
    outside = AppRef("game", "game.service", path="/srv/game")
    assert not by_key(available_items(outside, FACTS, True, False))[WORKDIR].available


def test_probe_is_read_only_and_parses():
    script = build_probe_script(APP)
    for forbidden in ("rm ", "systemctl stop", "mv ", ">>", "tee"):
        assert forbidden not in script
    out = "home=/h\nsudo=yes\nunit=ours\nworkdir=repo\nkeyfiles=yes\nkeyblock=no\nenv0=yes\n"
    facts = parse_probe(out, 1)
    assert facts.unit == "ours" and facts.key_files and not facts.key_block
    assert facts.env_present == (True,)


# --- the script, really run in a throw-away home -------------------------------------------
@pytest.mark.skipif(SH is None, reason="no local sh")
class TestRealRun:
    def make_home(self, tmp_path):
        home = tmp_path / "home"
        (home / "apps/game/.git").mkdir(parents=True)
        (home / "apps/game/src.txt").write_text("code")
        (home / "apps/game/.env").write_text("A=1")
        (home / "apps/game/.env.raspi-bak").write_text("A=0")
        (home / "data").mkdir()
        (home / "data/precious").write_text("keep me")
        ssh = home / ".ssh"
        ssh.mkdir()
        (ssh / "raspi_deploy_game").write_text("private")
        (ssh / "raspi_deploy_game.pub").write_text("public")
        (ssh / "raspi_deploy_other").write_text("other key")
        block = (
            f"{KEY_MARKER}game\nHost github-game\n  HostName github.com\n  User git\n"
            "  IdentityFile ~/.ssh/raspi_deploy_game\n  IdentitiesOnly yes\n"
        )
        (ssh / "config").write_text(
            "Host mine\n  HostName example.org\n\n"
            + block
            + "\nHost other\n  HostName b\n"
        )
        return home

    def shell_home(self, home):
        """$HOME as the shell sees it (MSYS rewrites Windows paths, the script text is not)."""
        env = {**os.environ, "HOME": home.as_posix()}
        out = subprocess.run(
            [SH, "-c", "echo $HOME"], env=env, capture_output=True, check=False
        )
        return out.stdout.decode().strip()

    def run(self, home, script):
        env = {**os.environ, "HOME": home.as_posix()}
        return subprocess.run(
            [SH, "-c", 'eval "$(cat)"'],
            input=script.encode(),
            env=env,
            capture_output=True,
            check=False,
        )

    def app_in(self, home):
        env = EnvFile("default", (home / "apps/game/.env").as_posix())
        return AppRef("game", "game.service", env_files=(env,)), RemoveFacts(
            home=home.as_posix(), env_present=(True,)
        )

    def test_it_removes_the_ticked_things_and_nothing_else(self, tmp_path):
        home = self.make_home(tmp_path)
        app, facts = self.app_in(home)
        result = self.run(home, build_remove_script(app, {WORKDIR, KEY, ENV}, facts))
        assert result.returncode == 0, result.stderr.decode()
        assert not (home / "apps/game").exists()
        assert not (home / ".ssh/raspi_deploy_game").exists()
        assert not (home / ".ssh/raspi_deploy_game.pub").exists()
        assert (home / ".ssh/raspi_deploy_other").exists()
        assert (home / "data/precious").read_text() == "keep me"
        config = (home / ".ssh/config").read_text()
        assert "Host mine" in config and "Host other" in config
        assert "github-game" not in config and KEY_MARKER not in config

    def test_without_the_ticks_nothing_is_touched(self, tmp_path):
        home = self.make_home(tmp_path)
        app, facts = self.app_in(home)
        result = self.run(home, build_remove_script(app, set(), facts))
        assert result.returncode == 0
        assert (home / "apps/game/src.txt").exists() and (
            home / ".ssh/raspi_deploy_game"
        ).exists()

    def test_the_working_copy_is_refused_outside_home_and_when_not_git(self, tmp_path):
        home = self.make_home(tmp_path)
        outside = tmp_path / "srv"
        (outside / ".git").mkdir(parents=True)
        app = AppRef("game", "game.service", path=outside.as_posix())
        result = self.run(home, build_remove_script(app, {WORKDIR}, RemoveFacts()))
        assert result.returncode == 5 and (outside / ".git").exists()
        plain = home / "plain"
        plain.mkdir()
        app = AppRef("game", "game.service", path=self.shell_home(home) + "/plain")
        result = self.run(home, build_remove_script(app, {WORKDIR}, RemoveFacts()))
        assert result.returncode == 6 and plain.exists()

    def test_the_key_block_is_kept_if_someone_edited_it(self, tmp_path):
        home = self.make_home(tmp_path)
        (home / ".ssh/config").write_text(
            f"{KEY_MARKER}game\nHost something-else\n  User x\n"
        )
        app, facts = self.app_in(home)
        self.run(home, build_remove_script(app, {KEY}, facts))
        assert "something-else" in (home / ".ssh/config").read_text()


def test_the_unit_part_stops_disables_and_deletes_only_that_unit():
    script = build_remove_script(APP, {UNIT}, FACTS)
    assert (
        "systemctl stop game.service" in script
        and "systemctl disable game.service" in script
    )
    assert "rm -f /etc/systemd/system/game.service" in script
    assert "rm -rf" not in script and "$HOME" not in script


# --- the service -------------------------------------------------------------------------------
class Pi:
    def __init__(self, fail=False):
        self.fail, self.scripts, self.manifest = fail, [], None

    def run_script(self, host, script, timeout=20.0):
        self.scripts.append(script)
        if "unit=ours" in script or "echo unit=" in script:
            return RunResult(
                True,
                "home=/home/pi\nsudo=yes\nunit=ours\nworkdir=repo\nkeyfiles=yes\nkeyblock=yes\nenv0=yes\n",
            )
        if "removed" in script:
            return (
                RunResult(False, "", "boom")
                if self.fail
                else RunResult(True, "removed\n")
            )
        if "base64 -d" in script:
            encoded = re.search(r"printf %s '?([A-Za-z0-9+/=]+)'? \|", script)[1]
            self.manifest = base64.b64decode(encoded).decode()
            return RunResult(True, "written\n")
        if self.manifest is None:
            return RunResult(True, "@@MISSING\n")
        return RunResult(True, "@@EXISTS\n" + self.manifest)


def test_plan_reads_the_pi_and_apply_does_the_pi_first_and_the_list_last(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("RASPI_HOME", str(tmp_path))
    pi = Pi()
    plan = plan_remove(HOST, pi, APP, True)
    assert {i.key for i in plan.items if i.available} >= {
        INVENTORY,
        UNIT,
        WORKDIR,
        KEY,
        ENV,
    }
    result = apply_remove(HOST, pi, plan, {INVENTORY, UNIT})
    assert result.ok and "Removed game" in result.messages[-1]
    assert "systemctl stop" in pi.scripts[1] and "apps: []" in pi.manifest


def test_a_failed_removal_on_the_pi_leaves_the_app_listed(monkeypatch, tmp_path):
    monkeypatch.setenv("RASPI_HOME", str(tmp_path))
    pi = Pi(fail=True)
    plan = plan_remove(HOST, pi, APP, True)
    result = apply_remove(HOST, pi, plan, {INVENTORY, UNIT})
    assert (
        not result.ok and "still listed" in result.messages[-1] and pi.manifest is None
    )


def test_a_selection_outside_what_is_available_is_refused(monkeypatch, tmp_path):
    monkeypatch.setenv("RASPI_HOME", str(tmp_path))
    plan = plan_remove(HOST, Pi(), APP, False)  # the app is declared on this PC
    with pytest.raises(RemoveError, match="inventory"):
        apply_remove(HOST, Pi(), plan, {INVENTORY})


def test_local_copies_are_deleted_on_request(monkeypatch, tmp_path):
    monkeypatch.setenv("RASPI_HOME", str(tmp_path))
    envstore.save_local("pi", "game", {"A": "1"})
    plan = plan_remove(HOST, Pi(), APP, True)
    assert by_key(plan.items)[LOCAL].available
    result = apply_remove(HOST, Pi(), plan, {LOCAL})
    assert result.ok and not envstore.local_copies("pi", "game", ["default"])


# --- the dialog, the window and the CLI ------------------------------------------------------------
@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def make_plan(monkeypatch, tmp_path, inventory_on_pi=True):
    monkeypatch.setenv("RASPI_HOME", str(tmp_path))
    return plan_remove(HOST, Pi(), APP, inventory_on_pi)


def test_dialog_ticks_two_things_and_needs_the_name_typed(qapp, monkeypatch, tmp_path):
    from raspi.gui.remove_dialog import RemoveAppDialog

    dialog = RemoveAppDialog(make_plan(monkeypatch, tmp_path), "pi")
    assert dialog.chosen() == {INVENTORY, UNIT}
    assert not dialog.remove_button.isEnabled()  # name not typed yet
    dialog.confirm_edit.setText("gam")
    assert not dialog.remove_button.isEnabled()
    dialog.confirm_edit.setText("game")
    assert dialog.remove_button.isEnabled()
    assert "systemctl stop game.service" in dialog.preview.toPlainText()
    dialog._boxes[UNIT].setChecked(False)
    assert "nothing runs on the Pi" in dialog.preview.toPlainText()
    dialog._boxes[UNIT].setChecked(True)
    dialog._boxes[WORKDIR].setChecked(True)
    assert "rm -rf" in dialog.preview.toPlainText()
    dialog._accept()
    assert dialog.result_value == {INVENTORY, UNIT, WORKDIR}
    for box in dialog._boxes.values():
        box.setChecked(False)
    assert not dialog.remove_button.isEnabled()  # nothing ticked


def test_unavailable_items_cannot_be_ticked(qapp, monkeypatch, tmp_path):
    from raspi.gui.remove_dialog import RemoveAppDialog

    dialog = RemoveAppDialog(
        make_plan(monkeypatch, tmp_path, inventory_on_pi=False), "pi"
    )
    assert not dialog._boxes[INVENTORY].isEnabled() and INVENTORY not in dialog.chosen()
    labels = " ".join(label.text() for label in dialog.findChildren(QLabel))
    assert (
        "/mnt/data" in labels and "Never touched" in labels
    )  # the data is named as safe


def test_the_window_removes_after_the_dialog_is_accepted(qapp, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QDialog

    from raspi.gui import remove_actions
    from raspi.gui.main_window import MainWindow
    from raspi.gui.theme import LIGHT

    shown = []
    monkeypatch.setattr(remove_actions, "submit", lambda fn, ok, bad=None: ok(fn()))
    monkeypatch.setattr(
        remove_actions.QMessageBox, "information", lambda *a: shown.append(a[2])
    )
    monkeypatch.setattr(
        remove_actions.QMessageBox, "critical", lambda *a: shown.append(a[2])
    )

    def accept_with_two_items(self):
        self.result_value = {INVENTORY, UNIT}
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(remove_actions.RemoveAppDialog, "exec", accept_with_two_items)
    pi = Pi()
    window = MainWindow([HOST], pi, LIGHT, auto_refresh=False)
    monkeypatch.setattr(MainWindow, "reload_config", lambda self: True)
    window._on_remove_planned((HOST, make_plan(monkeypatch, tmp_path)))
    assert any("systemctl stop game.service" in s for s in pi.scripts)
    assert shown and "Removed game" in shown[0]
    window.close()


def test_cli_dry_run_then_wrong_name_removes_nothing(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from raspi.cli import main as cli

    monkeypatch.setenv("RASPI_HOME", str(tmp_path))
    pi = Pi()
    monkeypatch.setattr(cli, "_live_host", lambda name: (HOST, HOST))
    monkeypatch.setattr("raspi.cli.remove.SshTransport", lambda: pi)
    monkeypatch.setattr(
        "raspi.cli.remove.read_inventory",
        lambda h, t: type("I", (), {"source": "pi"})(),
    )
    dry = CliRunner().invoke(cli.app, ["host", "remove-app", "pi", "game", "--dry-run"])
    assert dry.exit_code == 0 and "systemctl stop game.service" in dry.output
    assert not any("removed" in s for s in pi.scripts)
    wrong = CliRunner().invoke(
        cli.app, ["host", "remove-app", "pi", "game"], input="nope\n"
    )
    assert wrong.exit_code == 1 and not any("removed" in s for s in pi.scripts)
    ok = CliRunner().invoke(
        cli.app, ["host", "remove-app", "pi", "game"], input="game\n"
    )
    assert ok.exit_code == 0 and any("removed" in s for s in pi.scripts)
