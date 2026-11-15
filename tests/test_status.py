from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from raspi.config.loader import ConfigError, parse_hosts
from raspi.core.models import AppRef, Host
from raspi.core.remote import build_status_script, parse_status
from raspi.services.status import collect_host
from raspi.ssh.transport import RunResult
from raspi.web.app import create_app

APPS = (AppRef("game", "game.service", "/home/u/game"), AppRef("bot", "bot.service"))
OUTPUT = """@@APP game
ActiveState=active
SubState=running
UnitFileState=enabled
MainPID=651
ActiveEnterTimestamp=Fri 2026-10-16 10:00:00 CEST
NRestarts=2
rev=5d3886f
dirty=3
remote=https://user:secret@github.com/me/game.git
@@APP bot
ActiveState=failed
SubState=failed
UnitFileState=enabled
MainPID=0
NRestarts=
"""


def test_parse_status_maps_fields_and_hides_credentials():
    game, bot = parse_status(OUTPUT, APPS)
    assert (game.state, game.pid, game.restarts, game.rev, game.dirty) == (
        "running",
        651,
        2,
        "5d3886f",
        3,
    )
    assert game.remote == "https://github.com/me/game.git"
    assert (bot.state, bot.rev, bot.dirty) == ("failed", "", None)


def test_script_is_read_only_and_quotes_values():
    script = build_status_script((AppRef("x", "x.service", "/home/u/it's"),))
    assert "'/home/u/it'\"'\"'s'" in script
    for forbidden in ("sudo", "restart", "rm ", ">>", "git pull", "git checkout"):
        assert forbidden not in script


class FakeTransport:
    def __init__(self, result: RunResult):
        self.result = result

    def run_script(self, host, script, timeout=20.0):
        return self.result


HOST = Host("pi", "10.0.0.1", "u", APPS)


def test_collect_host_unreachable():
    hs = collect_host(HOST, FakeTransport(RunResult(False, "", "Permission denied")))
    assert not hs.reachable and "Permission denied" in hs.error
    assert isinstance(hs.checked_at, datetime)


@pytest.mark.parametrize("bad", ["a b", "../x", "x;rm", ""])
def test_config_rejects_unsafe_values(bad):
    data = {
        "hosts": [
            {
                "name": "pi",
                "address": "10.0.0.1",
                "user": "u",
                "apps": [{"name": "a", "unit": bad}],
            }
        ]
    }
    with pytest.raises(ConfigError):
        parse_hosts(data)


def test_web_page_shows_state_as_glyph_and_word(monkeypatch, tmp_path):
    (tmp_path / "hosts.yaml").write_text(
        "hosts:\n- {name: pi, address: 10.0.0.1, user: u, apps: [{name: game, unit: game.service}]}\n"
    )
    monkeypatch.setenv("RASPI_HOME", str(tmp_path))
    client = TestClient(
        create_app(FakeTransport(RunResult(True, OUTPUT))),
        base_url="http://127.0.0.1:8765",
    )
    html = client.get("/").text
    assert "● running" in html and "game" in html


def test_a_unit_that_does_not_exist_is_shown_as_no_unit_and_start_explains_it():
    from raspi.services.actions import run_action

    out = "@@APP game\nLoadState=not-found\nActiveState=inactive\nSubState=dead\n"
    (status,) = parse_status(out, APPS[:1])
    assert status.state == "no-unit"
    assert "LoadState" in build_status_script(APPS)

    class Pi:
        def run_script(self, host, script, timeout=20.0):
            return RunResult(
                True,
                "Failed to start game.service: Unit game.service not found.\n"
                "exit=5\nActiveState=inactive\n",
            )

    result = run_action(Host("pi", "10.0.0.1", "u"), APPS[0], "start", Pi())
    assert not result.ok and "Install unit" in result.message
