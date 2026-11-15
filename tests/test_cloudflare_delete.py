import pytest
from test_cloudflare import HOSTS, SETTINGS, FakeCloudflare, make

from raspi.core.routes import (
    RouteError,
)
from raspi.services import routing_delete

# --- deleting a tunnel -------------------------------------------------------------------
TID = "22222222-3333-4444-5555-666666666666"


def _tunnel(
    status="inactive", name="raspi-manager-raspi-v--web-game", source="cloudflare"
):
    return {"id": TID, "name": name, "config_src": source, "status": status}


def _delete_fake(status="inactive", **kw):
    return FakeCloudflare(
        [_tunnel(status, **kw)],
        {
            TID: [
                {"hostname": "game.example.com", "service": "http://localhost:2567"},
                {"service": "http_status:404"},
            ]
        },
        [
            {
                "id": "r1",
                "type": "CNAME",
                "name": "game.example.com",
                "content": f"{TID.upper()}.cfargotunnel.com",
                "proxied": True,
            },
            {
                "id": "r2",
                "type": "CNAME",
                "name": "other.example.com",
                "content": "elsewhere.example.net",
                "proxied": True,
            },
        ],
    )


def test_delete_plan_lists_routes_and_dns_that_would_be_left_dangling_and_changes_nothing():
    fake = _delete_fake()
    plan = routing_delete.plan_delete_tunnel(make(fake), SETTINGS, TID, HOSTS)
    assert plan.name == "raspi-manager-raspi-v--web-game" and plan.status == "inactive"
    assert [r.hostname for r in plan.routes] == ["game.example.com"]
    assert plan.dns_hostnames == ["game.example.com"]
    assert all(m == "GET" for m, _, _ in fake.calls)


@pytest.mark.parametrize("status", ["healthy", "degraded"])
def test_delete_is_refused_while_a_connector_is_active(status):
    with pytest.raises(RouteError, match="connector is still running"):
        routing_delete.plan_delete_tunnel(
            make(_delete_fake(status)), SETTINGS, TID, HOSTS
        )


def test_delete_is_refused_for_foreign_or_missing_tunnels():
    with pytest.raises(RouteError, match="not created by raspi-manager"):
        routing_delete.plan_delete_tunnel(
            make(_delete_fake(name="web-game")), SETTINGS, TID, HOSTS
        )
    with pytest.raises(RouteError, match="not created by raspi-manager"):
        routing_delete.plan_delete_tunnel(
            make(_delete_fake(name="raspi-manager-unknownpi")), SETTINGS, TID, HOSTS
        )
    with pytest.raises(RouteError, match="no longer exists"):
        routing_delete.plan_delete_tunnel(make(FakeCloudflare()), SETTINGS, TID, HOSTS)


def test_apply_deletes_once_reports_dangling_dns_and_never_touches_dns():
    fake = _delete_fake("down")
    client = make(fake)
    plan = routing_delete.plan_delete_tunnel(client, SETTINGS, TID, HOSTS)
    message = routing_delete.apply_delete_tunnel(client, SETTINGS, plan)
    writes = [(m, p.split("/")[-1]) for m, p, _ in fake.calls if m != "GET"]
    assert writes == [("DELETE", TID)] and fake.tunnels == []
    assert "game.example.com" in message and "should be removed or repointed" in message
    assert not any("dns_records" in p and m != "GET" for m, p, _ in fake.calls)


def test_apply_rechecks_the_status_so_a_connector_started_after_the_preview_blocks_the_delete():
    fake = _delete_fake("inactive")
    client = make(fake)
    plan = routing_delete.plan_delete_tunnel(client, SETTINGS, TID, HOSTS)
    fake.tunnels[0]["status"] = "healthy"  # a connector came up in between
    with pytest.raises(RouteError, match="became healthy"):
        routing_delete.apply_delete_tunnel(client, SETTINGS, plan)
    assert not any(m == "DELETE" for m, _, _ in fake.calls)
