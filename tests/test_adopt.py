import base64
import os
import re

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from typer.testing import CliRunner

from raspi.cli import main as cli
from raspi.config.loader import parse_manifest
from raspi.core.manifest import render_manifest
from raspi.core.models import AppRef, Host
from raspi.core.scan import Candidate, build_scan_script, parse_scan
from raspi.gui.adopt_dialog import AdoptDialog
from raspi.services.adopt import add_apps, candidate_to_app, scan_host
from raspi.ssh.transport import RunResult

SCAN = """@@UNIT example-bot.service
ActiveState=active
User=pi
WorkingDirectory=/home/pi/dev/Example-Bot
ExecStart={ path=/usr/bin/node ; argv[]=/usr/bin/node /home/pi/dev/Example-Bot/src/index.js ; ignore_errors=no ; start_time=[n/a] }
repo=/home/pi/dev/Example-Bot
@@UNIT cloudflared.service
ActiveState=activating
User=
WorkingDirectory=
ExecStart={ path=/usr/local/bin/cloudflared ; argv[]=/usr/local/bin/cloudflared --config x ; ignore_errors=no }
@@UNIT chat-bot.service
ActiveState=inactive
User=pi
WorkingDirectory=!/home/pi/dev/chat-bot
ExecStart={ path=/usr/bin/node ; argv[]=/usr/bin/node index.js ; ignore_errors=no }
repo=
@@UNIT bad name.service
ActiveState=active
User=pi
"""
HOST = Host("pi", "10.0.0.1", "pi", (AppRef("old", "old.service"),))


class Pi:
    def __init__(self, scan: str = SCAN, manifest: tuple[AppRef, ...] | None = None):
        self.scan = scan
        self.text = None if manifest is None else render_manifest(manifest)
        self.scripts = []

    def run_script(self, host, script, timeout=20.0):
        self.scripts.append(script)
        if "systemctl show" in script and "@@UNIT" in script:
            return RunResult(True, self.scan)
        if "base64 -d" in script:
            self.text = base64.b64decode(
                re.search(r"printf %s '?([A-Za-z0-9+/=]+)'? \|", script)[1]
            ).decode()
            return RunResult(True, "written\n")
        return RunResult(
            True, "@@MISSING\n" if self.text is None else "@@EXISTS\n" + self.text
        )

    def apps(self):
        return parse_manifest(yaml.safe_load(self.text))


def test_scan_script_is_read_only():
    script = build_scan_script()
    assert "/etc/systemd/system/*.service" in script and "systemctl show" in script
    for forbidden in ("sudo", "restart", " rm ", ">>", "git pull", "git checkout"):
        assert forbidden not in script


def test_parse_scan_reads_settings_and_command():
    ai, cf, vx, bad = parse_scan(SCAN)
    assert (ai.unit, ai.name, ai.user, ai.state) == (
        "example-bot.service",
        "example-bot",
        "pi",
        "active",
    )
    assert ai.repo == "/home/pi/dev/Example-Bot"
    assert ai.command == "/usr/bin/node /home/pi/dev/Example-Bot/src/index.js"
    assert cf.user == "" and vx.workdir == "/home/pi/dev/chat-bot" and vx.repo == ""
    assert bad.unit == "bad name.service"


def test_scan_keeps_only_unmanaged_units_of_the_login_user_with_valid_names():
    result = scan_host(HOST, Pi())
    assert result.ok
    assert [c.name for c in result.candidates] == ["example-bot", "chat-bot"]
    managed = Host("pi", "x", "pi", (AppRef("example-bot", "example-bot.service"),))
    assert [c.name for c in scan_host(managed, Pi()).candidates] == ["chat-bot"]


def test_scan_reports_an_unreachable_pi():
    class Down:
        def run_script(self, *a, **k):
            return RunResult(False, "", "timeout")

    result = scan_host(HOST, Down())
    assert not result.ok and "timeout" in result.message


def test_candidate_becomes_an_app_with_its_repo_as_path():
    ai = parse_scan(SCAN)[0]
    assert candidate_to_app(ai) == AppRef(
        "example-bot", "example-bot.service", "/home/pi/dev/Example-Bot"
    )
    vx = parse_scan(SCAN)[2]
    assert candidate_to_app(vx).path is None


def test_add_apps_appends_to_the_pi_inventory_with_a_backup():
    pi = Pi(manifest=(AppRef("game", "game.service"),))
    new = (AppRef("example-bot", "example-bot.service", "/home/pi/dev/Example-Bot"),)
    result = add_apps(HOST, new, pi)
    assert result.ok and "Added example-bot" in result.message
    assert [a.name for a in pi.apps()] == ["game", "example-bot"]


def test_add_apps_creates_the_inventory_from_hosts_yaml_when_missing():
    pi = Pi()
    result = add_apps(HOST, (AppRef("chat-bot", "chat-bot.service"),), pi)
    assert result.ok and [a.name for a in pi.apps()] == ["old", "chat-bot"]


def test_add_apps_refuses_duplicates_and_dry_run_writes_nothing():
    pi = Pi(manifest=(AppRef("game", "game.service"),))
    before = pi.text
    dup = add_apps(HOST, (AppRef("other", "game.service"),), pi)
    assert not dup.ok and "already in the inventory" in dup.message
    dry = add_apps(HOST, (AppRef("chat-bot", "chat-bot.service"),), pi, dry_run=True)
    assert dry.ok and "nothing was written" in dry.message and pi.text == before


def test_dialog_lists_candidates_and_returns_the_ticked_ones():
    QApplication.instance() or QApplication([])
    dialog = AdoptDialog(HOST, Pi())
    dialog._on_scanned(scan_host(HOST, Pi()))
    assert dialog.table.rowCount() == 2 and dialog.add_button.isEnabled()
    dialog._accept()  # nothing ticked yet
    assert dialog.result_value == () and "Tick" in dialog.hint.text()
    dialog.table.item(1, 0).setCheckState(Qt.CheckState.Checked)
    dialog._accept()
    assert [a.name for a in dialog.result_value] == ["chat-bot"]


def test_dialog_says_when_there_is_nothing_to_add():
    QApplication.instance() or QApplication([])
    dialog = AdoptDialog(HOST, Pi(scan=""))
    dialog._on_scanned(scan_host(HOST, Pi(scan="")))
    assert not dialog.add_button.isEnabled() and "nothing to add" in dialog.hint.text()


@pytest.fixture
def cli_pi(monkeypatch, tmp_path):
    monkeypatch.setenv("RASPI_HOME", str(tmp_path))
    (tmp_path / "hosts.yaml").write_text(
        "hosts:\n- {name: pi, address: 10.0.0.1, user: pi}\n"
    )
    pi = Pi()
    monkeypatch.setattr(cli, "SshTransport", lambda: pi)
    return pi


def test_cli_scan_then_adopt(cli_pi):
    runner = CliRunner()
    scan = runner.invoke(cli.app, ["host", "scan", "pi"])
    assert scan.exit_code == 0 and "example-bot\tactive" in scan.output
    assert "cloudflared" not in scan.output
    dry = runner.invoke(cli.app, ["host", "adopt", "pi", "example-bot", "--dry-run"])
    assert dry.exit_code == 0 and cli_pi.text is None
    real = runner.invoke(
        cli.app, ["host", "adopt", "pi", "example-bot", "chat-bot.service", "--yes"]
    )
    assert real.exit_code == 0 and "Added example-bot, chat-bot" in real.output
    assert [a.name for a in cli_pi.apps()] == ["example-bot", "chat-bot"]
    unknown = runner.invoke(cli.app, ["host", "adopt", "pi", "ghost", "--yes"])
    assert unknown.exit_code == 1 and "Not found" in unknown.output


def test_candidate_dataclass_is_hashable():
    assert len({Candidate("a.service", "u", "active", "", "", "")}) == 1
