import subprocess

import pytest

from raspi.core.models import AppRef, AppStatus, EnvFile
from raspi.core.remote import _unit_read_lines, build_status_script, parse_status
from raspi.core.stage import READY, next_step
from raspi.core.unit import render_unit, unit_drift

APP = AppRef(
    "game",
    "game.service",
    repo="me/game",
    build="npm ci",
    run='node "a b".js --x=$Y',
    built="abc1234",
    env_files=(EnvFile("default", "/home/pi/apps/game/.env"),),
)
UNIT_PATH = "/etc/systemd/system/game.service"


def read_unit(text: str, tmp_path, app: AppRef = APP) -> AppStatus:
    """Run the real read-only lines against a unit file in a throw-away folder."""
    unit_file = tmp_path / "game.service"
    unit_file.write_text(text, encoding="utf-8", newline="\n")
    script = "\n".join(_unit_read_lines(app)).replace(UNIT_PATH, unit_file.as_posix())
    out = subprocess.run(
        ["sh", "-c", script], capture_output=True, text=True, check=True
    ).stdout
    status = parse_status("@@APP game\n" + out, (app,))[0]
    status.home = "/home/pi"  # the test shell's $HOME is not the Pi's
    return status


def ours(app: AppRef = APP) -> str:
    return render_unit(app, "pi", "/home/pi/apps/game", "/home/pi/.nvm/bin")


def test_a_unit_rendered_from_the_inventory_has_no_drift(tmp_path):
    assert unit_drift(APP, read_unit(ours(), tmp_path)) == []


def test_a_changed_run_command_is_named(tmp_path):
    old = AppRef(**{**APP.__dict__, "run": "npm start"})
    assert unit_drift(APP, read_unit(ours(old), tmp_path)) == ["run command"]


def test_changed_env_files_and_working_folder_are_named(tmp_path):
    old = AppRef(**{**APP.__dict__, "env_files": (), "path": "/srv/other"})
    text = render_unit(old, "pi", "/srv/other", "")
    assert unit_drift(APP, read_unit(text, tmp_path)) == [
        "environment files",
        "working folder",
    ]


def test_node_folder_is_not_drift(tmp_path):
    text = render_unit(APP, "pi", "/home/pi/apps/game", "/elsewhere/bin")
    assert unit_drift(APP, read_unit(text, tmp_path)) == []


def test_a_foreign_unit_is_never_judged(tmp_path):
    foreign = ours().replace("# managed by raspi-manager", "# mine")
    status = read_unit(foreign, tmp_path)
    assert not status.unit_ours
    assert unit_drift(APP, status) == []


def test_an_app_without_run_has_no_drift():
    status = AppStatus("game", "game.service", unit_ours=True, unit_exec="x")
    assert unit_drift(AppRef("game", "game.service"), status) == []


def test_status_script_only_reads_the_unit():
    script = "\n".join(_unit_read_lines(APP))
    assert "sudo" not in script
    assert "tee" not in script and ">" not in script.replace("2>/dev/null", "")
    assert "\n".join(_unit_read_lines(APP)) in build_status_script((APP,))


def test_the_row_says_the_unit_is_out_of_date_and_names_the_field(tmp_path):
    old = AppRef(**{**APP.__dict__, "run": "npm start"})
    status = read_unit(ours(old), tmp_path)
    status.active, status.load, status.rev = "active", "loaded", "abc1234"
    status.env_present = (True,)
    step = next_step(APP, status)
    assert step.label == "Unit out of date (Install unit…)"
    assert "run command" in step.why and "not restarted" in step.why
    assert "npm" not in step.why  # names only, never the command itself
    assert next_step(old, status).label == READY


@pytest.mark.parametrize("active", ["active", "inactive", "failed"])
def test_drift_shows_whatever_the_state(active, tmp_path):
    old = AppRef(**{**APP.__dict__, "run": "npm start"})
    status = read_unit(ours(old), tmp_path)
    status.active, status.load, status.rev = active, "loaded", "abc1234"
    status.env_present = (True,)
    assert next_step(APP, status).label.startswith("Unit out of date")
