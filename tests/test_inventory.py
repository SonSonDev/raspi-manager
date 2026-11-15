import pytest

from raspi.config.loader import ConfigError, parse_manifest
from raspi.core.models import AppRef, Host
from raspi.core.remote import build_manifest_read_script
from raspi.services.inventory import read_inventory, resolve_all
from raspi.services.status import collect_host
from raspi.ssh.transport import RunResult

PC_APP = AppRef("old", "old.service")
HOST = Host("pi", "10.0.0.1", "u", (PC_APP,))
MANIFEST = """@@EXISTS
version: 1
apps:
- name: game
  unit: game.service
  path: /home/u/game
  env_files:
  - {label: server, path: /home/u/game/server/.env}
"""
STATUS = "@@APP game\nActiveState=active\nSubState=running\nMainPID=1\n"


class Fake:
    """Answers the manifest read first, then the status script."""

    def __init__(self, manifest: RunResult, status: RunResult | None = None):
        self.manifest, self.status, self.scripts = manifest, status, []

    def run_script(self, host, script, timeout=20.0):
        self.scripts.append(script)
        return self.manifest if len(self.scripts) == 1 else self.status


def test_read_script_is_read_only():
    script = build_manifest_read_script()
    assert ".raspi/apps.yaml" in script
    for forbidden in ("sudo", ">", "rm ", "mv "):
        assert forbidden not in script


def test_pi_manifest_replaces_the_pc_list():
    inv = read_inventory(HOST, Fake(RunResult(True, MANIFEST)))
    assert inv.source == "pi" and inv.error == ""
    (app,) = inv.host.apps
    assert (app.name, app.unit, app.path) == ("game", "game.service", "/home/u/game")
    assert [(e.label, e.path) for e in app.env_files] == [
        ("server", "/home/u/game/server/.env")
    ]
    assert HOST.apps == (PC_APP,)  # the configured host is not mutated


def test_missing_manifest_keeps_the_pc_list():
    inv = read_inventory(HOST, Fake(RunResult(True, "@@MISSING\n")))
    assert inv.source == "pc" and inv.host.apps == (PC_APP,) and inv.error == ""


@pytest.mark.parametrize(
    "text",
    [
        "@@EXISTS\nversion: 2\napps: []\n",
        "@@EXISTS\napps: []\n",
        "@@EXISTS\nversion: 1\napps: [{name: a, unit: 'x;rm'}]\n",
        "@@EXISTS\nversion: 1\napps: [{name: a, unit: a.service}, {name: a, unit: b.service}]\n",
        "@@EXISTS\n: : not yaml [\n",
    ],
)
def test_invalid_manifest_is_ignored_and_reported(text):
    inv = read_inventory(HOST, Fake(RunResult(True, text)))
    assert inv.source == "pc" and inv.host.apps == (PC_APP,)
    assert "apps.yaml on pi is ignored" in inv.error


def test_unreachable_pi_is_reported_with_one_ssh_call():
    fake = Fake(RunResult(False, "", "Permission denied"))
    hs = collect_host(HOST, fake)
    assert not hs.reachable and "Permission denied" in hs.error
    assert len(fake.scripts) == 1 and hs.host.apps == (PC_APP,)


def test_collect_host_uses_the_pi_apps_and_says_so():
    hs = collect_host(HOST, Fake(RunResult(True, MANIFEST), RunResult(True, STATUS)))
    assert hs.inventory == "pi" and [a.name for a in hs.apps] == ["game"]
    assert [a.name for a in hs.host.apps] == ["game"]


def test_collect_host_surfaces_an_invalid_manifest():
    bad = "@@EXISTS\nversion: 9\n"
    hs = collect_host(HOST, Fake(RunResult(True, bad), RunResult(True, "@@APP old\n")))
    assert hs.inventory == "pc" and "ignored" in hs.inventory_error


def test_resolve_all_returns_effective_hosts():
    (resolved,) = resolve_all([HOST], Fake(RunResult(True, MANIFEST)))
    assert [a.name for a in resolved.apps] == ["game"]


def test_parse_manifest_accepts_an_empty_app_list():
    assert parse_manifest({"version": 1, "apps": None}) == ()
    with pytest.raises(ConfigError):
        parse_manifest("not a mapping")


def test_table_rows_follow_the_pi_inventory():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from datetime import datetime

    from raspi.gui.fleet_model import FleetModel
    from raspi.gui.theme import LIGHT

    model = FleetModel([HOST], LIGHT)
    assert [r.key for r in model.rows] == [("pi", "old")]
    model.set_host_status(
        collect_host(HOST, Fake(RunResult(True, MANIFEST), RunResult(True, STATUS)))
    )
    assert [r.key for r in model.rows] == [("pi", "game")]
    assert model.rows[0].app.env_files[0].label == "server"
    assert model.status_summary(datetime(2026, 11, 14, 9, 5, 7)) == (
        "Last checked 09:05:07"
    )
    bad = collect_host(
        HOST, Fake(RunResult(True, "@@EXISTS\nversion: 9\n"), RunResult(True, ""))
    )
    model.set_host_status(bad)
    assert "ignored" in model.status_summary(datetime.now())


# --- publishing the PC's apps to the Pi -----------------------------------------
from raspi.core.manifest import render_manifest
from raspi.core.models import EnvFile
from raspi.core.remote import build_manifest_push_script
from raspi.services.inventory import plan_publish, publish

PUB_APPS = (
    AppRef(
        "game",
        "game.service",
        "/home/u/game",
        (EnvFile("server", "/home/u/game/server/.env"),),
    ),
    AppRef("bot", "bot.service"),
)
PUB_HOST = Host("pi", "10.0.0.1", "u", PUB_APPS)


class Recorder:
    """Plays a Pi: remembers every script, answers reads from `files`."""

    def __init__(self, existing: str | None = None, fail_write: bool = False):
        self.scripts, self.existing, self.fail_write = [], existing, fail_write

    def run_script(self, host, script, timeout=20.0):
        self.scripts.append(script)
        if "base64 -d" in script:
            if self.fail_write:
                return RunResult(False, "", "disk full")
            self.existing = self._decode(script)
            return RunResult(True, "written: /home/u/.raspi/apps.yaml\n")
        if self.existing is None:
            return RunResult(True, "@@MISSING\n")
        return RunResult(True, "@@EXISTS\n" + self.existing)

    @staticmethod
    def _decode(script: str) -> str:
        import base64
        import re

        return base64.b64decode(
            re.search(r"printf %s '?([A-Za-z0-9+/=]+)'? \|", script)[1]
        ).decode()


def test_rendered_inventory_round_trips_and_has_no_values():
    text = render_manifest(PUB_APPS)
    assert text.startswith("# App inventory")
    import yaml

    assert parse_manifest(yaml.safe_load(text)) == PUB_APPS


def test_push_script_is_atomic_private_and_never_uses_sudo():
    script = build_manifest_push_script("version: 1\napps: []\n")
    for needed in ("set -e", "umask 077", "mktemp", 'mv "$tmp" "$target"', "chmod 600"):
        assert needed in script
    assert "sudo" not in script and "rm " not in script
    assert 'cp -p "$target" "$target.bak"' in script


def test_plan_then_publish_writes_and_verifies():
    pi = Recorder()
    plan = plan_publish(PUB_HOST, pi)
    assert plan.ok and plan.content == render_manifest(PUB_APPS)
    assert not any("base64 -d" in s for s in pi.scripts)  # planning is read-only
    result = publish(PUB_HOST, plan, pi)
    assert result.ok and "2 app(s)" in result.message
    assert pi.existing == plan.content
    after = read_inventory(Host("pi", "10.0.0.1", "u"), pi)
    assert after.source == "pi" and after.host.apps == PUB_APPS


def test_publish_dry_run_writes_nothing():
    pi = Recorder()
    result = publish(PUB_HOST, plan_publish(PUB_HOST, pi), pi, dry_run=True)
    assert result.ok and not result.executed and pi.existing is None


def test_an_existing_inventory_is_never_replaced_from_the_pc():
    pi = Recorder(existing="version: 1\napps: []\n")
    plan = plan_publish(PUB_HOST, pi)
    assert not plan.ok and "source of truth" in plan.message
    assert not publish(PUB_HOST, plan, pi).executed


def test_publishing_an_identical_inventory_says_so():
    pi = Recorder(existing=render_manifest(PUB_APPS))
    assert "exactly this inventory" in plan_publish(PUB_HOST, pi).message


def test_publish_reports_write_failure_and_unreadable_pi():
    pi = Recorder(fail_write=True)
    result = publish(PUB_HOST, plan_publish(PUB_HOST, pi), pi)
    assert not result.ok and "disk full" in result.message
    down = Fake(RunResult(False, "", "timeout"))
    assert "Cannot read" in plan_publish(PUB_HOST, down).message
    assert not plan_publish(Host("pi", "a", "u"), pi).ok


def test_cli_publish_dry_run_then_real(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from raspi.cli import main as cli

    monkeypatch.setenv("RASPI_HOME", str(tmp_path))
    (tmp_path / "hosts.yaml").write_text(
        "hosts:\n- {name: pi, address: 10.0.0.1, user: u, apps: [{name: game, unit: game.service}]}\n"
    )
    pi = Recorder()
    monkeypatch.setattr(cli, "SshTransport", lambda: pi)
    runner = CliRunner()
    dry = runner.invoke(cli.app, ["host", "publish", "pi", "--dry-run"])
    assert dry.exit_code == 0 and "name: game" in dry.output and pi.existing is None
    real = runner.invoke(cli.app, ["host", "publish", "pi", "--yes"])
    assert real.exit_code == 0 and "Published 1 app(s)" in real.output
    again = runner.invoke(cli.app, ["host", "publish", "pi", "--yes"])
    assert again.exit_code == 1 and "exactly this inventory" in again.output


def test_publish_button_follows_where_the_apps_live():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from raspi.gui.main_window import MainWindow
    from raspi.gui.theme import LIGHT

    QApplication.instance() or QApplication([])
    w = MainWindow([PUB_HOST], object(), LIGHT, auto_refresh=False)
    w.select_key(("pi", "game"))
    assert not w.btn_publish.isEnabled()  # not checked yet
    w.model.set_host_status(collect_host(PUB_HOST, Recorder()))
    w._update_buttons()
    assert w.btn_publish.isEnabled()  # reachable, apps only on the PC
    pi = Recorder(existing=render_manifest(PUB_APPS))
    w.model.set_host_status(collect_host(PUB_HOST, pi))
    w._update_buttons()
    assert not w.btn_publish.isEnabled()  # already on the Pi
    w.close()


def test_repo_build_and_run_survive_a_manifest_round_trip():
    import yaml

    from raspi.config.loader import ConfigError, parse_manifest
    from raspi.core.manifest import render_manifest
    from raspi.core.models import AppRef

    app = AppRef(
        "notes-app",
        "notes-app.service",
        "/home/pi/apps/notes-app",
        repo="SonSonDev/notes-app",
        build="npm ci && npm run build",
        run="npm start",
    )
    assert parse_manifest(yaml.safe_load(render_manifest((app,)))) == (app,)
    for bad in (
        {"repo": "not a repo"},
        {"build": "npm ci\nrm -rf ~"},
        {"run": "x" * 501},
    ):
        entry = {"name": "a", "unit": "a.service", **bad}
        with pytest.raises(ConfigError):
            parse_manifest({"version": 1, "apps": [entry]})
