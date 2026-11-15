import json

import httpx
import pytest

from raspi.cloudflare.client import CloudflareClient, CloudflareError
from raspi.config import cloudflare_settings as cs
from raspi.core.routes import (
    Route,
    RouteError,
    check_cf_id,
    check_hostname,
    check_service,
    routes_of,
    with_route,
    without_route,
)
from raspi.services import routing

TOKEN = "cf-secret-token-123"
ACC = "a" * 32
ZONE = "b" * 32
SETTINGS = cs.CloudflareSettings(ACC, ZONE, "example.com")
HOSTS = ["raspi", "raspi-v"]


class FakeCloudflare:
    """Just enough of the Cloudflare API, with state, behind httpx.MockTransport."""

    def __init__(self, tunnels=None, ingress=None, dns=None, fail=None):
        self.tunnels = tunnels if tunnels is not None else []
        self.ingress = ingress or {}
        self.dns = dns or []
        self.calls = []
        self.fail = fail

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(
            (request.method, request.url.path, request.headers.get("authorization"))
        )
        body = json.loads(request.content) if request.content else None
        path = request.url.path.replace("/client/v4", "")
        if self.fail:
            return httpx.Response(
                self.fail[0],
                json={
                    "success": False,
                    "errors": [{"code": 1, "message": self.fail[1]}],
                },
            )
        ok = lambda result: httpx.Response(
            200, json={"success": True, "result": result}
        )
        if path == f"/accounts/{ACC}/cfd_tunnel" and request.method == "GET":
            name = request.url.params.get("name")
            return ok([t for t in self.tunnels if not name or t["name"] == name])
        if path == f"/accounts/{ACC}/cfd_tunnel" and request.method == "POST":
            tunnel = {
                "id": "c" * 32,
                "name": body["name"],
                "config_src": body["config_src"],
                "token": "tunnel-conn-token",
            }
            self.tunnels.append({k: v for k, v in tunnel.items() if k != "token"})
            return ok(tunnel)
        if path.endswith("/configurations") and request.method == "GET":
            tid = path.split("/")[-2]
            return ok({"config": {"ingress": self.ingress.get(tid, [])}})
        if path.endswith("/configurations") and request.method == "PUT":
            self.ingress[path.split("/")[-2]] = body["config"]["ingress"]
            return ok({})
        if (
            path.startswith(f"/accounts/{ACC}/cfd_tunnel/")
            and request.method == "DELETE"
        ):
            tid = path.rsplit("/", 1)[-1]
            gone = next(t for t in self.tunnels if t["id"] == tid)
            self.tunnels.remove(gone)
            return ok(gone)
        if path.endswith("/token"):
            return ok("tunnel-conn-token")
        if path == f"/zones/{ZONE}/dns_records" and request.method == "GET":
            name = request.url.params.get("name")
            return ok([r for r in self.dns if not name or r["name"] == name])
        if path == f"/zones/{ZONE}/dns_records" and request.method == "POST":
            self.dns.append(body)
            return ok(body)
        if path.startswith(f"/zones/{ZONE}/dns_records/") and request.method == "PATCH":
            record_id = path.rsplit("/", 1)[-1]
            record = next(r for r in self.dns if r.get("id") == record_id)
            record.update(body)
            return ok(record)
        return httpx.Response(
            404,
            json={"success": False, "errors": [{"code": 404, "message": "no route"}]},
        )


def make(fake: FakeCloudflare) -> CloudflareClient:
    return CloudflareClient(
        TOKEN, httpx.Client(transport=httpx.MockTransport(fake.handler))
    )


# --- pure route logic -------------------------------------------------------------------
def test_ingress_helpers_keep_catch_all_last_and_other_rules():
    base = [
        {
            "hostname": "b.example.com",
            "service": "http://localhost:2",
            "originRequest": {"x": 1},
        },
        {"service": "http_status:404"},
    ]
    out = with_route(base, Route("a.example.com", "http://localhost:1"))
    assert [r.get("hostname") for r in out] == ["a.example.com", "b.example.com", None]
    assert out[-1] == {"service": "http_status:404"} and out[1]["originRequest"] == {
        "x": 1
    }
    assert (
        with_route(out, Route("a.example.com", "http://localhost:9"))[0]["service"]
        == "http://localhost:9"
    )
    assert [r.get("hostname") for r in without_route(out, "a.example.com")] == [
        "b.example.com",
        None,
    ]
    assert routes_of(out) == [
        Route("a.example.com", "http://localhost:1"),
        Route("b.example.com", "http://localhost:2"),
    ]


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "localhost",
        "a b.example.com",
        "http://x.com",
        "x.com/path",
        "-a.example.com",
    ],
)
def test_hostname_validation(bad):
    with pytest.raises(RouteError):
        check_hostname(bad)


def test_service_and_id_validation():
    assert check_hostname(" Game.Example.com ") == "game.example.com"
    assert check_service("http://localhost:2567") == "http://localhost:2567"
    for bad in (
        "http://example.com:80",
        "ftp://localhost:1",
        "localhost:80",
        "http://localhost",
        "http://localhost:80; rm",
    ):
        with pytest.raises(RouteError):
            check_service(bad)
    assert check_cf_id("A" * 32, "x") == "a" * 32
    with pytest.raises(RouteError):
        check_cf_id("short", "account ID")


# --- client --------------------------------------------------------------------------------
def test_client_sends_bearer_and_never_leaks_token_in_errors():
    fake = FakeCloudflare(fail=(403, "Authentication error"))
    with pytest.raises(CloudflareError) as exc:
        make(fake).list_tunnels(ACC)
    assert "Authentication error" in str(exc.value) and "HTTP 403" in str(exc.value)
    assert TOKEN not in str(exc.value) and fake.calls[0][2] == f"Bearer {TOKEN}"


def test_client_network_error_is_wrapped():
    def boom(request):
        raise httpx.ConnectError("secret detail " + TOKEN)

    client = CloudflareClient(TOKEN, httpx.Client(transport=httpx.MockTransport(boom)))
    with pytest.raises(CloudflareError) as exc:
        client.list_tunnels(ACC)
    assert TOKEN not in str(exc.value)


# --- planning and applying -----------------------------------------------------------------------
def test_plan_for_a_brand_new_route_lists_every_change_and_changes_nothing():
    fake = FakeCloudflare()
    plan = routing.plan_add_route(
        make(fake), SETTINGS, "raspi-v", "game.example.com", "http://localhost:2567"
    )
    assert plan.create_tunnel and plan.create_dns and len(plan.steps) == 3
    assert "raspi-manager-raspi-v" in plan.steps[0]
    assert all(m == "GET" for m, _, _ in fake.calls)


def test_apply_creates_tunnel_then_ingress_then_dns_in_that_order():
    fake = FakeCloudflare()
    client = make(fake)
    plan = routing.plan_add_route(
        client, SETTINGS, "raspi-v", "game.example.com", "http://localhost:2567"
    )
    result = routing.apply_add_route(client, SETTINGS, plan)
    writes = [(m, p.split("/")[-1]) for m, p, _ in fake.calls if m != "GET"]
    assert writes == [
        ("POST", "cfd_tunnel"),
        ("PUT", "configurations"),
        ("POST", "dns_records"),
    ]
    assert result.tunnel_id == "c" * 32 and result.created_tunnel
    assert fake.ingress["c" * 32][-1] == {"service": "http_status:404"}
    assert (
        fake.dns[0]["content"] == f"{'c' * 32}.cfargotunnel.com"
        and fake.dns[0]["proxied"] is True
    )
    assert "tunnel-conn-token" not in " ".join(result.messages)


def test_plan_is_idempotent_when_everything_exists():
    tunnel = {
        "id": "c" * 32,
        "name": "raspi-manager-raspi-v",
        "config_src": "cloudflare",
    }
    fake = FakeCloudflare(
        [tunnel],
        {
            "c" * 32: [
                {"hostname": "game.example.com", "service": "http://localhost:2567"},
                {"service": "http_status:404"},
            ]
        },
        [
            {
                "type": "CNAME",
                "name": "game.example.com",
                "content": f"{'c' * 32}.cfargotunnel.com",
            }
        ],
    )
    plan = routing.plan_add_route(
        make(fake), SETTINGS, "raspi-v", "game.example.com", "http://localhost:2567"
    )
    assert plan.nothing_to_do and not plan.create_tunnel


def test_conflicts_are_refused_not_overwritten():
    tunnel = {
        "id": "c" * 32,
        "name": "raspi-manager-raspi-v",
        "config_src": "cloudflare",
    }
    other_dns = FakeCloudflare(
        [tunnel],
        {},
        [
            {
                "type": "CNAME",
                "name": "game.example.com",
                "content": "elsewhere.example.net",
            }
        ],
    )
    with pytest.raises(RouteError, match="already has a DNS record"):
        routing.plan_add_route(
            make(other_dns),
            SETTINGS,
            "raspi-v",
            "game.example.com",
            "http://localhost:2567",
        )
    a_record = FakeCloudflare(
        [], {}, [{"type": "A", "name": "game.example.com", "content": "1.2.3.4"}]
    )
    with pytest.raises(RouteError, match="already has a DNS record"):
        routing.plan_add_route(
            make(a_record),
            SETTINGS,
            "raspi-v",
            "game.example.com",
            "http://localhost:2567",
        )
    local = FakeCloudflare(
        [{"id": "d" * 32, "name": "raspi-manager-raspi-v", "config_src": "local"}]
    )
    with pytest.raises(RouteError, match="managed locally"):
        routing.plan_add_route(
            make(local),
            SETTINGS,
            "raspi-v",
            "game.example.com",
            "http://localhost:2567",
        )
    with pytest.raises(RouteError, match="not inside the zone"):
        routing.plan_add_route(
            make(FakeCloudflare()),
            SETTINGS,
            "raspi-v",
            "game.other.org",
            "http://localhost:1",
        )
    with pytest.raises(RouteError, match="account ID and the zone"):
        routing.plan_add_route(
            make(FakeCloudflare()),
            cs.CloudflareSettings(),
            "raspi-v",
            "game.example.com",
            "http://localhost:1",
        )


def test_overview_and_remove_route_keep_dns_untouched():
    tunnel = {
        "id": "c" * 32,
        "name": "raspi-manager-raspi-v",
        "config_src": "cloudflare",
        "status": "healthy",
    }
    fake = FakeCloudflare(
        [
            tunnel,
            {"id": "d" * 32, "name": "old", "config_src": "local", "status": "down"},
        ],
        {
            "c" * 32: [
                {"hostname": "game.example.com", "service": "http://localhost:2567"},
                {"service": "http_status:404"},
            ]
        },
    )
    client = make(fake)
    info = routing.overview(client, SETTINGS)
    assert [(t.name, t.status, len(t.routes)) for t in info] == [
        ("old", "down", 0),
        ("raspi-manager-raspi-v", "healthy", 1),
    ]
    msg = routing.remove_route(client, SETTINGS, "c" * 32, "game.example.com")
    assert "DNS record was not touched" in msg and fake.ingress["c" * 32] == [
        {"service": "http_status:404"}
    ]
    assert not any(p.endswith("dns_records") for _, p, _ in fake.calls)
    with pytest.raises(RouteError):
        routing.remove_route(client, SETTINGS, "c" * 32, "nope.example.com")
