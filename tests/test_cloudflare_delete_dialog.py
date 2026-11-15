# ruff: noqa: F401, F811 - the shared fixtures are imported from test_cloudflare_dialog
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QInputDialog

from raspi.config import cloudflare_settings as cs
from raspi.core.routes import Route
from raspi.gui import cloudflare_dialog as dlg
from raspi.services import routing_delete
from raspi.services.routing import TunnelInfo

ACC, ZONE = "a" * 32, "b" * 32
from test_cloudflare_dialog import HOSTS, env, pump, qapp


def _delete_setup(monkeypatch, env):
    env.pw = "t"
    cs.save_settings(cs.CloudflareSettings(ACC, ZONE, "example.com"))
    tunnels = [
        TunnelInfo(
            "t" * 32,
            "raspi-manager-raspi-v--web-game",
            "inactive",
            [Route("game.example.com", "http://localhost:2567")],
        )
    ]
    plan = routing_delete.DeletePlan(
        "t" * 32,
        "raspi-manager-raspi-v--web-game",
        "inactive",
        tunnels[0].routes,
        ["game.example.com"],
    )
    deleted = []
    monkeypatch.setattr(dlg.routing, "overview", lambda client, settings: tunnels)
    monkeypatch.setattr(
        routing_delete, "plan_delete_tunnel", lambda client, settings, tid, hosts: plan
    )
    monkeypatch.setattr(
        routing_delete,
        "apply_delete_tunnel",
        lambda client, settings, p: deleted.append(p.tunnel_id) or "Tunnel deleted",
    )
    d = dlg.CloudflareDialog(HOSTS)
    assert pump(lambda: d.table.rowCount() == 1)
    d.table.selectRow(0)
    return d, deleted


def test_delete_needs_the_tunnel_name_typed_exactly(qapp, env, monkeypatch):
    d, deleted = _delete_setup(monkeypatch, env)
    shown = []
    monkeypatch.setattr(
        QInputDialog,
        "getText",
        lambda *a, **k: shown.append(a[2]) or ("wrong", True),
    )
    d.delete_selected_tunnel()
    assert pump(lambda: "did not match" in d.note.text())
    assert (
        deleted == []
        and "cannot be undone" in shown[0]
        and "game.example.com" in shown[0]
        and "NOT deleted" in shown[0]
    )


def test_delete_cancelled_changes_nothing_and_confirmed_deletes_once(
    qapp, env, monkeypatch
):
    d, deleted = _delete_setup(monkeypatch, env)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("", False))
    d.delete_selected_tunnel()
    assert pump(lambda: "Cancelled" in d.note.text()) and deleted == []
    monkeypatch.setattr(
        QInputDialog,
        "getText",
        lambda *a, **k: ("raspi-manager-raspi-v--web-game", True),
    )
    d.delete_selected_tunnel()
    assert pump(lambda: deleted == ["t" * 32])
    assert pump(lambda: "Tunnel deleted" in d.note.text())


def test_delete_without_selection_explains(qapp, env, monkeypatch):
    d, deleted = _delete_setup(monkeypatch, env)
    d.table.clearSelection()
    d.delete_selected_tunnel()
    assert "Select a row" in d.note.text() and deleted == []
