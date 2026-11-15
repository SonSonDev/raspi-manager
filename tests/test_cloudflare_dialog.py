import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication, QMessageBox

from raspi.cloudflare import client as cf_client
from raspi.config import cloudflare_settings as cs
from raspi.core.models import Host
from raspi.core.routes import Route
from raspi.gui import cloudflare_dialog as dlg
from raspi.gui.add_route_dialog import AddRouteDialog
from raspi.gui.dns_dialog import DnsRecordsDialog
from raspi.services.routing import ApplyResult, RoutePlan, TunnelInfo

ACC, ZONE = "a" * 32, "b" * 32
HOSTS = [Host("raspi-v", "10.0.0.2", "u")]


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def pump(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        QCoreApplication.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


class Mem:
    def __init__(self):
        self.pw = None

    def get_password(self, s, u):
        return self.pw

    def set_password(self, s, u, p):
        self.pw = p

    def delete_password(self, s, u):
        self.pw = None


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("RASPI_HOME", str(tmp_path))
    mem = Mem()
    monkeypatch.setattr(cs, "keyring", mem)
    return mem


def test_token_is_saved_masked_cleared_and_never_shown(qapp, env):
    d = dlg.CloudflareDialog(HOSTS)
    assert "No token" in d.token_state.text()
    d.token_edit.setText("super-secret-token")
    d.save_token()
    assert env.pw == "super-secret-token"
    assert (
        d.token_edit.text() == ""
        and "super-secret-token" not in d.token_state.text() + d.note.text()
    )
    assert d.token_edit.echoMode().name == "Password"
    assert d.btn_remove_token.isEnabled()


def test_lookup_fills_zone_and_account_and_saves(qapp, env, monkeypatch):
    env.pw = "t"
    monkeypatch.setattr(
        cf_client.CloudflareClient,
        "list_zones",
        lambda self, name=None: [{"id": ZONE, "name": name, "account": {"id": ACC}}],
    )
    monkeypatch.setattr(dlg.routing, "overview", lambda client, settings: [])
    d = dlg.CloudflareDialog(HOSTS)
    d.zone_name.setText("Example.com")
    d.lookup()
    assert pump(lambda: d.zone_id.text() == ZONE)
    assert d.account_id.text() == ACC
    assert cs.load_settings() == cs.CloudflareSettings(ACC, ZONE, "example.com")


def test_lookup_without_token_or_zone_explains_itself(qapp, env):
    d = dlg.CloudflareDialog(HOSTS)
    d.lookup()
    assert "token first" in d.note.text()
    env.pw = "t"
    d.lookup()
    assert "domain" in d.note.text()


def test_overview_table_lists_tunnels_and_routes(qapp, env, monkeypatch):
    env.pw = "t"
    cs.save_settings(cs.CloudflareSettings(ACC, ZONE, "example.com"))
    tunnels = [
        TunnelInfo(
            "c" * 32,
            "raspi-manager-raspi-v",
            "healthy",
            [Route("game.example.com", "http://localhost:2567")],
        ),
        TunnelInfo("d" * 32, "empty", "inactive", []),
    ]
    monkeypatch.setattr(dlg.routing, "overview", lambda client, settings: tunnels)
    d = dlg.CloudflareDialog(HOSTS)
    assert pump(lambda: d.table.rowCount() == 2)
    assert (
        d.table.item(0, 2).text() == "game.example.com"
        and d.table.item(1, 2).text() == "—"
    )
    assert "inactive" in d.note.text()


def test_add_route_requires_confirmation_before_any_change(qapp, env, monkeypatch):
    env.pw = "t"
    cs.save_settings(cs.CloudflareSettings(ACC, ZONE, "example.com"))
    applied = []
    plan = RoutePlan(
        "raspi-manager-raspi-v",
        Route("game.example.com", "http://localhost:2567"),
        ["Create the tunnel 'x'", "Route game.example.com"],
        True,
    )
    monkeypatch.setattr(dlg.routing, "overview", lambda client, settings: [])
    monkeypatch.setattr(dlg.routing, "plan_add_route", lambda *a: plan)
    monkeypatch.setattr(
        dlg.routing,
        "apply_add_route",
        lambda client, settings, p: (
            applied.append(p) or ApplyResult("c" * 32, True, ["done"])
        ),
    )

    def fake_exec(self):
        self.result_value = ("raspi-v", "game.example.com", "http://localhost:2567", "")
        return dlg.QDialog.DialogCode.Accepted

    monkeypatch.setattr(AddRouteDialog, "exec", fake_exec)
    d = dlg.CloudflareDialog(HOSTS)
    shown = {}
    monkeypatch.setattr(
        QMessageBox,
        "exec",
        lambda self: shown.setdefault("text", self.informativeText()) and 0,
    )
    monkeypatch.setattr(
        QMessageBox,
        "clickedButton",
        lambda self: next(b for b in self.buttons() if b.text() == "Cancel"),
    )
    d.add_route()
    assert pump(lambda: "Cancelled" in d.note.text())
    assert applied == [] and "Create the tunnel" in shown["text"]
    monkeypatch.setattr(
        QMessageBox,
        "clickedButton",
        lambda self: next(b for b in self.buttons() if b.text() == "Apply"),
    )
    d.add_route()
    assert pump(lambda: len(applied) == 1)
    assert pump(lambda: "no connector yet" in d.note.text())


def test_add_route_dialog_validates_presence(qapp):
    d = AddRouteDialog(HOSTS, "example.com")
    d._accept()
    assert d.result_value is None and not d.error.isHidden()
    d.hostname.setText("game.example.com")
    d.service.setText("http://localhost:2567")
    d._accept()
    assert d.result_value == (
        "raspi-v",
        "game.example.com",
        "http://localhost:2567",
        "",
    )


def test_edit_dialog_locks_host_and_hostname(qapp):
    d = AddRouteDialog(
        HOSTS,
        "example.com",
        None,
        ("raspi-v", "game.example.com", "http://localhost:2567", ""),
    )
    assert (
        d.windowTitle() == "Edit a route"
        and not d.host_box.isEnabled()
        and d.hostname.isReadOnly()
    )
    d.service.setText("http://localhost:3000")
    d._accept()
    assert d.result_value == (
        "raspi-v",
        "game.example.com",
        "http://localhost:3000",
        "",
    )


def test_edit_selected_route_prefills_and_refuses_foreign_tunnels(
    qapp, env, monkeypatch
):
    env.pw = "t"
    cs.save_settings(cs.CloudflareSettings(ACC, ZONE, "example.com"))
    tunnels = [
        TunnelInfo(
            "c" * 32,
            "raspi-manager-raspi-v",
            "healthy",
            [Route("game.example.com", "http://localhost:2567")],
        ),
        TunnelInfo(
            "d" * 32,
            "someone-else",
            "healthy",
            [Route("x.example.com", "http://localhost:1")],
        ),
    ]
    monkeypatch.setattr(dlg.routing, "overview", lambda client, settings: tunnels)
    d = dlg.CloudflareDialog(HOSTS)
    assert pump(lambda: d.table.rowCount() == 2)
    seen = []
    monkeypatch.setattr(d, "add_route", lambda prefill=None: seen.append(prefill))
    d.table.selectRow(1)
    d.edit_selected_route()
    assert seen == [] and "not created by raspi-manager" in d.note.text()
    d.table.selectRow(0)
    d.edit_selected_route()
    assert seen == [("raspi-v", "game.example.com", "http://localhost:2567", "")]
    d.table.clearSelection()
    d.edit_selected_route()
    assert "Select a route" in d.note.text()


def test_dns_dialog_lists_filters_and_is_prefilled(qapp, env, monkeypatch):
    entries = [
        routing_entry("web-game.example.com", "CNAME", "tunnel web-game"),
        routing_entry("www.example.com", "A", ""),
    ]
    monkeypatch.setattr(dlg.routing, "dns_overview", lambda client, settings: entries)
    s = cs.CloudflareSettings(ACC, ZONE, "example.com")
    d = DnsRecordsDialog(object(), s, "web-game")
    assert pump(lambda: "1 of 2" in d.status.text())
    assert d.table.rowCount() == 1 and d.table.item(0, 4).text() == "tunnel web-game"
    d.filter.setText("")
    assert d.table.rowCount() == 2
    d.filter.setText("a")  # matches type A and others
    assert d.table.rowCount() >= 1


def routing_entry(name, kind, note):
    return dlg.routing.DnsEntry(kind, name, "x.example.net", True, note)


def _setup_conflict(monkeypatch, repointable=True):
    env_plan = RoutePlan(
        "raspi-manager-raspi-v",
        Route("game.example.com", "http://localhost:2567"),
        [
            "Repoint the existing DNS record game.example.com from old.cfargotunnel.com to new.cfargotunnel.com"
        ],
        True,
    )
    calls = []

    def plan(client, settings, host, hostname, service, repoint=False, project=None):
        calls.append(repoint)
        if repoint:
            return env_plan
        raise dlg.routing.DnsConflictError(
            "game.example.com already has a DNS record: CNAME → old", repointable
        )

    monkeypatch.setattr(dlg.routing, "plan_add_route", plan)
    monkeypatch.setattr(dlg.routing, "overview", lambda client, settings: [])

    def fake_exec(self):
        self.result_value = ("raspi-v", "game.example.com", "http://localhost:2567", "")
        return dlg.QDialog.DialogCode.Accepted

    monkeypatch.setattr(AddRouteDialog, "exec", fake_exec)
    return calls


def _click(monkeypatch, label, texts):
    monkeypatch.setattr(
        QMessageBox, "exec", lambda self: texts.append(self.informativeText()) or 0
    )
    monkeypatch.setattr(
        QMessageBox,
        "clickedButton",
        lambda self: next(b for b in self.buttons() if b.text().startswith(label)),
    )


def test_conflict_offers_repoint_then_shows_the_preview_before_any_change(
    qapp, env, monkeypatch
):
    env.pw = "t"
    cs.save_settings(cs.CloudflareSettings(ACC, ZONE, "example.com"))
    calls = _setup_conflict(monkeypatch)
    applied = []
    monkeypatch.setattr(
        dlg.routing,
        "apply_add_route",
        lambda c, s, p: applied.append(p) or ApplyResult("c" * 32, True, ["done"]),
    )
    d = dlg.CloudflareDialog(HOSTS)
    texts = []
    _click(monkeypatch, "Repoint", texts)
    d.add_route()
    assert pump(lambda: calls == [False, True])
    assert applied == []  # asking is not applying
    assert (
        "already has a DNS record" in texts[0]
        and "before anything is modified" in texts[0]
    )
    assert (
        pump(lambda: len(texts) == 2) and "Repoint the existing DNS record" in texts[1]
    )
    # the preview itself still needs an explicit Apply: the fake click picked "Repoint…"-prefixed buttons only,
    # so no button matched "Apply" and the preview was cancelled
    assert applied == []


def test_declining_the_repoint_changes_nothing(qapp, env, monkeypatch):
    env.pw = "t"
    cs.save_settings(cs.CloudflareSettings(ACC, ZONE, "example.com"))
    calls = _setup_conflict(monkeypatch)
    d = dlg.CloudflareDialog(HOSTS)
    _click(monkeypatch, "Cancel", [])
    d.add_route()
    assert pump(lambda: "Cancelled" in d.note.text())
    assert calls == [False]


def test_non_repointable_conflict_only_explains(qapp, env, monkeypatch):
    env.pw = "t"
    cs.save_settings(cs.CloudflareSettings(ACC, ZONE, "example.com"))
    calls = _setup_conflict(monkeypatch, repointable=False)
    d = dlg.CloudflareDialog(HOSTS)
    shown = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: shown.append(1) or 0)
    d.add_route()
    assert pump(lambda: "already has a DNS record" in d.note.text())
    assert shown == [] and calls == [False]


def test_add_route_dialog_previews_the_tunnel_name_and_returns_the_project(qapp):
    hosts = [Host("raspi", "10.0.0.1", "u"), Host("raspi-v", "10.0.0.2", "u")]
    d = AddRouteDialog(hosts, "example.com")
    d.host_box.setCurrentText("raspi-v")
    assert d.tunnel_label.text() == "raspi-manager-raspi-v"
    d.project.setText("web-game")
    assert d.tunnel_label.text() == "raspi-manager-raspi-v--web-game"
    d.hostname.setText("game.example.com")
    d.service.setText("http://localhost:2567")
    d._accept()
    assert d.result_value == (
        "raspi-v",
        "game.example.com",
        "http://localhost:2567",
        "web-game",
    )


def test_edit_derives_pi_and_project_from_the_tunnel_name(qapp, env, monkeypatch):
    env.pw = "t"
    cs.save_settings(cs.CloudflareSettings(ACC, ZONE, "example.com"))
    tunnels = [
        TunnelInfo(
            "c" * 32,
            "raspi-manager-raspi-v--web-game",
            "healthy",
            [Route("game.example.com", "http://localhost:2567")],
        )
    ]
    monkeypatch.setattr(dlg.routing, "overview", lambda client, settings: tunnels)
    d = dlg.CloudflareDialog(
        [Host("raspi", "10.0.0.1", "u"), Host("raspi-v", "10.0.0.2", "u")]
    )
    assert pump(lambda: d.table.rowCount() == 1)
    seen = []
    monkeypatch.setattr(d, "add_route", lambda prefill=None: seen.append(prefill))
    d.table.selectRow(0)
    d.edit_selected_route()
    assert seen == [
        ("raspi-v", "game.example.com", "http://localhost:2567", "web-game")
    ]
