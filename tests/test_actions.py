import re

import pytest
from fastapi.testclient import TestClient

from raspi.core.models import AppRef, Host
from raspi.core.remote import build_action_script, build_logs_script, parse_action
from raspi.services.actions import LookupError_, find_app, read_logs, run_action
from raspi.ssh.transport import RunResult
from raspi.web.app import create_app

APP = AppRef("game", "game.service", "/home/u/game")
HOST = Host("pi", "10.0.0.1", "u", (APP,))


class RecordingTransport:
    def __init__(self, stdout="", ok=True, error=""):
        self.calls = []
        self._result = RunResult(ok, stdout, error)

    def run_script(self, host, script, timeout=20.0):
        self.calls.append(script)
        return self._result


def test_action_script_whitelist_and_quoting():
    assert "sudo -n systemctl restart game.service" in build_action_script(
        "game.service", "restart"
    )
    with pytest.raises(ValueError):
        build_action_script("game.service", "disable")
    with pytest.raises(ValueError):
        build_action_script("game.service", "restart; rm -rf /")
    assert "'a b.service'" in build_action_script("a b.service", "start")


def test_logs_script_is_read_only_and_clamped():
    script = build_logs_script("game.service", 99999)
    assert "-n 1000" in script and "sudo" not in script


def test_parse_action_success_and_sudo_failure():
    assert parse_action("exit=0\nActiveState=active\nSubState=running\n") == (
        True,
        "",
        "active",
    )
    ok, msg, state = parse_action(
        "sudo: a password is required\nexit=1\nActiveState=inactive\n"
    )
    assert not ok and "password" in msg and state == "inactive"


def test_dry_run_executes_nothing():
    transport = RecordingTransport()
    result = run_action(HOST, APP, "restart", transport, dry_run=True)
    assert (
        transport.calls == []
        and not result.executed
        and "systemctl restart" in result.script
    )


def test_run_action_reports_sudo_hint():
    transport = RecordingTransport(
        "sudo: a password is required\nexit=1\nActiveState=active\n"
    )
    result = run_action(HOST, APP, "stop", transport)
    assert not result.ok and "passwordless sudo" in result.message


def test_read_logs_and_find_app():
    assert (
        read_logs(HOST, APP, RecordingTransport("line1\nline2")).message
        == "line1\nline2"
    )
    assert find_app([HOST], "pi", "game") == (HOST, APP)
    with pytest.raises(LookupError_):
        find_app([HOST], "pi", "nope")


@pytest.fixture
def web(monkeypatch, tmp_path):
    (tmp_path / "hosts.yaml").write_text(
        "hosts:\n- {name: pi, address: 10.0.0.1, user: u, apps: [{name: game, unit: game.service}]}\n"
    )
    monkeypatch.setenv("RASPI_HOME", str(tmp_path))
    transport = RecordingTransport("exit=0\nActiveState=active\nSubState=running\n")
    client = TestClient(create_app(transport), base_url="http://127.0.0.1:8765")
    return client, transport


def test_post_requires_token(web):
    client, transport = web
    assert (
        client.post("/apps/pi/game/restart", data={"token": "wrong"}).status_code == 403
    )
    assert client.post("/apps/pi/game/restart").status_code == 403
    assert transport.calls == []


def test_confirm_then_post_runs_action(web):
    client, transport = web
    page = client.get("/apps/pi/game/confirm/restart")
    assert (
        page.status_code == 200 and "sudo systemctl restart game.service" in page.text
    )
    token = re.search(r'name="token" value="([^"]+)"', page.text).group(1)
    resp = client.post("/apps/pi/game/restart", data={"token": token})
    assert resp.status_code == 200 and "now" in resp.text and "active" in resp.text
    assert len(transport.calls) == 1 and "systemctl restart" in transport.calls[0]


def test_unknown_action_and_app(web):
    client, _ = web
    assert client.post("/apps/pi/game/disable", data={"token": "x"}).status_code == 404
    assert client.get("/apps/pi/ghost/logs").status_code == 404
    assert client.get("/apps/pi/game/confirm/start").status_code == 404


def test_foreign_host_header_rejected(web):
    client, _ = web
    assert client.get("/", headers={"host": "evil.example"}).status_code == 403


def test_logs_page_escapes_html(monkeypatch, tmp_path):
    (tmp_path / "hosts.yaml").write_text(
        "hosts:\n- {name: pi, address: 10.0.0.1, user: u, apps: [{name: game, unit: game.service}]}\n"
    )
    monkeypatch.setenv("RASPI_HOME", str(tmp_path))
    client = TestClient(
        create_app(RecordingTransport("<script>alert(1)</script>")),
        base_url="http://127.0.0.1:8765",
    )
    html = client.get("/apps/pi/game/logs").text
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html
