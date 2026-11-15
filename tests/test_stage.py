import pytest

from raspi.config.loader import ConfigError, parse_manifest
from raspi.core.manifest import render_manifest
from raspi.core.models import AppRef, AppStatus, EnvFile
from raspi.core.remote import build_status_script, parse_status
from raspi.core.stage import READY, next_step

ENV = (EnvFile("default", "/home/u/apps/game/.env"),)
APP = AppRef(
    "game",
    "game.service",
    repo="me/game",
    build="npm ci",
    run="npm start",
    built="abc1234",
    env_files=ENV,
)


def status(**kw) -> AppStatus:
    base = {
        "name": "game",
        "unit": "game.service",
        "load": "loaded",
        "active": "active",
        "rev": "abc1234",
        "key_present": True,
        "env_present": (True,),
    }
    base.update(kw)
    return AppStatus(**base)


@pytest.mark.parametrize(
    ("app", "st", "label"),
    [
        (APP, None, ""),
        (APP, status(rev="", key_present=False), "Deploy key…"),
        (APP, status(rev=""), "Install app…"),
        (
            AppRef("g", "g.service", repo="me/g", build="make"),
            status(),
            "Build (Install app…)",
        ),
        (APP, status(rev="def5678"), "Build (Install app…)"),
        (
            APP,
            status(rev="abc12345"),
            READY,
        ),  # a longer short-rev is still the same revision
        (
            AppRef("g", "g.service", repo="me/g", built="abc1234"),
            status(),
            "Set the run command (Install app…)",
        ),
        (
            APP,
            status(env_present=(False,), load="not-found", active="inactive"),
            "Push environment…",
        ),
        (APP, status(load="not-found", active="inactive"), "Install unit…"),
        (
            AppRef("g", "g.service", repo="me/g", run="npm start"),
            status(load="not-found", active="inactive"),
            "Environment…",
        ),
        (APP, status(active="inactive"), "Start"),
        (APP, status(active="failed"), "Start"),
    ],
)
def test_next_step_follows_the_workflow_order(app, st, label):
    assert next_step(app, st).label == label


def test_adopted_app_without_repo_only_needs_start_or_nothing():
    adopted = AppRef("old", "old.service")
    assert next_step(adopted, status(rev="")).label == READY
    assert next_step(adopted, status(active="inactive")).label == "Start"


def test_status_script_reads_key_env_and_default_working_copy_without_changing_anything():
    script = build_status_script((APP,))
    assert "raspi_deploy_game" in script and "envfile0" in script
    assert 'git -C "$HOME/apps/game" rev-parse' in script
    for forbidden in ("sudo", "rm ", ">>", "mkdir", "git pull"):
        assert forbidden not in script
    out = "@@APP game\nLoadState=loaded\nActiveState=active\nkey=yes\nenvfile0=no\nrev=abc1234\n"
    (parsed,) = parse_status(out, (APP,))
    assert (
        parsed.key_present is True
        and parsed.env_present == (False,)
        and parsed.rev == "abc1234"
    )


def test_built_revision_is_kept_in_the_inventory_and_validated():
    again = parse_manifest(__import__("yaml").safe_load(render_manifest((APP,))))
    assert again[0].built == "abc1234"
    entry = {"name": "a", "unit": "a.service", "built": "not a rev"}
    with pytest.raises(ConfigError):
        parse_manifest({"version": 1, "apps": [entry]})
