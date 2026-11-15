import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog
from test_app_route import (
    APP,
    CHECK_HAS_BIN,
    CHECK_RUNNING,
    SECRET,
    SETTINGS,
    FakeCloudflare,
    Pi,
)
from typer.testing import CliRunner

from raspi.cli import main as cli
from raspi.cli import route as cli_route
from raspi.core.models import AppRef, Host
from raspi.gui import route_actions
from raspi.gui.main_window import MainWindow
from raspi.gui.route_dialog import RouteSetupDialog
from raspi.gui.theme import LIGHT
from raspi.services.status import collect_host

HOST = Host("raspi-v", "10.0.0.2", "pi", (APP,))
PORTS = 'LISTEN 0 511 *:2567 *:* users:(("node",pid=5,fd=19))\n'


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_dialog_prefills_lists_ports_and_validates(qapp):
    d = RouteSetupDialog(HOST, APP, "example.com", Pi(ports=PORTS))
    assert d.hostname_edit.text() == "web-game.example.com"
    d._on_ports([2567, 3000])
    assert [d.port_box.itemText(i) for i in range(2)] == ["2567", "3000"]
    d._accept()
    assert d.result_value == ("web-game.example.com", 2567)
    d2 = RouteSetupDialog(HOST, APP, "example.com", Pi())
    d2.hostname_edit.setText("not a host")
    d2._accept()
    assert d2.result_value is None and not d2.error.isHidden()
    d2.hostname_edit.setText("game.example.com")
    d2.port_box.setEditText("99999")
    d2._accept()
    assert d2.result_value is None and "port" in d2.error.text()
    d2._on_ports([])
    assert "No listening port" in d2.hint.text()


def test_button_follows_reachability(qapp):
    w = MainWindow([HOST], Pi(CHECK_HAS_BIN), LIGHT, auto_refresh=False)
    w.select_key(("raspi-v", "web-game"))
    assert not w.btn_route.isEnabled()
    w.model.set_host_status(collect_host(HOST, Pi(CHECK_HAS_BIN)))
    w._update_buttons()
    assert w.btn_route.isEnabled()
    w.close()


@pytest.fixture
def flow(qapp, monkeypatch):
    """The whole GUI flow with fakes; background jobs run inline."""
    cf, pi = FakeCloudflare(), Pi(CHECK_HAS_BIN)
    shown = []
    monkeypatch.setattr(route_actions.store, "get_token", lambda: "t" * 20)
    monkeypatch.setattr(route_actions.store, "load_settings", lambda: SETTINGS)
    monkeypatch.setattr(route_actions, "CloudflareClient", lambda token: cf)

    def inline(fn, on_done, on_failed=None):
        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001 - same contract as workers.submit
            (on_failed or (lambda m: None))(str(exc))
        else:
            on_done(result)

    monkeypatch.setattr(route_actions, "submit", inline)
    for kind in ("information", "warning", "critical"):
        monkeypatch.setattr(
            route_actions.QMessageBox,
            kind,
            lambda *a, kind=kind: shown.append((kind, a[2])),
        )

    def fake_exec(self):
        self.result_value = ("game.example.com", 2567)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(RouteSetupDialog, "exec", fake_exec)
    w = MainWindow([HOST], pi, LIGHT, auto_refresh=False)
    monkeypatch.setattr(w, "_refresh_host", lambda name: None)
    w.select_key(("raspi-v", "web-game"))
    yield w, cf, pi, shown
    w.close()


def test_flow_applies_after_confirmation(flow, monkeypatch):
    w, cf, pi, shown = flow
    asked = []
    monkeypatch.setattr(w, "_confirm_route", lambda plan: asked.append(plan) or True)
    monkeypatch.setattr(
        route_actions.app_route, "_http_status", lambda url: 200, raising=True
    )
    monkeypatch.setattr(route_actions.app_route.time, "sleep", lambda s: None)
    w.setup_route()
    assert len(asked) == 1 and "Create the tunnel" in "\n".join(asked[0].steps)
    assert cf.calls == ["create_tunnel", "put_ingress", "create_cname"]
    assert len(pi.installs) == 1
    kind, text = shown[-1]
    assert kind == "information" and SECRET not in text and "installed" in text


def test_flow_changes_nothing_when_cancelled(flow, monkeypatch):
    w, cf, pi, _ = flow
    monkeypatch.setattr(w, "_confirm_route", lambda plan: False)
    w.setup_route()
    assert cf.calls == [] and pi.installs == []
    assert "Cancelled" in w.status_label.text()


def test_flow_explains_a_missing_cloudflare_setup(flow, monkeypatch):
    w, cf, _, shown = flow
    monkeypatch.setattr(route_actions.store, "get_token", lambda: None)
    w.setup_route()
    assert shown and "Cloudflare is not set up" in shown[-1][1] and cf.calls == []


def test_flow_reports_a_pi_that_cannot_run_the_connector(flow, monkeypatch):
    w, cf, pi, shown = flow
    pi.check = pi.check.replace("sudo=yes", "sudo=no")
    w.setup_route()
    assert shown[-1][0] == "critical" and "passwordless sudo" in shown[-1][1]
    assert cf.calls == []


def test_flow_says_so_when_already_set_up(flow):
    w, cf, pi, shown = flow
    pi.check = CHECK_RUNNING
    cf.create_tunnel(SETTINGS.account_id, "raspi-manager-raspi-v--web-game")
    tid = next(iter(cf.tunnels))
    cf.ingress[tid] = [
        {"hostname": "game.example.com", "service": "http://localhost:2567"},
        {"service": "http_status:404"},
    ]
    cf.create_cname(SETTINGS.zone_id, "game.example.com", f"{tid}.cfargotunnel.com")
    cf.calls.clear()
    w.setup_route()
    assert "nothing to change" in shown[-1][1] and cf.calls == []


# --- command line ------------------------------------------------------------------------
@pytest.fixture
def cli_env(monkeypatch, tmp_path):
    monkeypatch.setenv("RASPI_HOME", str(tmp_path))
    (tmp_path / "hosts.yaml").write_text(
        "hosts:\n- name: raspi-v\n  address: 10.0.0.2\n  user: pi\n"
        "  apps:\n  - {name: web-game, unit: web-game.service}\n"
    )
    cf, pi = FakeCloudflare(), Pi(CHECK_HAS_BIN, ports=PORTS)
    monkeypatch.setattr(cli_route, "SshTransport", lambda: pi)
    monkeypatch.setattr(cli_route, "_client_and_settings", lambda: (cf, SETTINGS))
    monkeypatch.setattr(cli_route.app_route.time, "sleep", lambda s: None)
    monkeypatch.setattr(cli_route.app_route, "_http_status", lambda url: 200)
    return cf, pi


def test_cli_route_add_dry_run_shows_everything_and_changes_nothing(cli_env):
    cf, pi = cli_env
    out = CliRunner().invoke(
        cli.app, ["route", "add", "game.example.com", "web-game", "--dry-run"]
    )
    assert out.exit_code == 0, out.output
    assert "Create the tunnel" in out.output and "token hidden" in out.output
    assert "port 2567" in out.output  # detected, not typed
    assert SECRET not in out.output and cf.calls == [] and pi.installs == []


def test_cli_route_add_applies_with_yes(cli_env):
    cf, pi = cli_env
    out = CliRunner().invoke(
        cli.app, ["route", "add", "game.example.com", "web-game:2567", "--yes"]
    )
    assert out.exit_code == 0 and "Route set up" in out.output
    assert cf.calls == ["create_tunnel", "put_ingress", "create_cname"]
    assert len(pi.installs) == 1 and SECRET not in out.output


def test_cli_route_add_asks_for_the_port_when_it_is_ambiguous(cli_env):
    _, pi = cli_env
    pi.ports = PORTS + 'LISTEN 0 511 *:3000 *:* users:(("node",pid=5,fd=20))\n'
    out = CliRunner().invoke(cli.app, ["route", "add", "game.example.com", "web-game"])
    assert out.exit_code == 1 and "Use web-game:PORT" in out.output
    unknown = CliRunner().invoke(
        cli.app, ["route", "add", "game.example.com", "ghost:1"]
    )
    assert unknown.exit_code == 1 and "no host" in unknown.output


def test_app_ref_is_hashable_for_the_dialog():
    assert len({AppRef("a", "a.service")}) == 1
