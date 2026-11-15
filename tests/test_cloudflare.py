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


# --- settings and token storage --------------------------------------------------------------------
class MemoryKeyring:
    def __init__(self):
        self.store = {}

    def get_password(self, service, user):
        return self.store.get((service, user))

    def set_password(self, service, user, pw):
        self.store[(service, user)] = pw

    def delete_password(self, service, user):
        import keyring.errors

        if (service, user) not in self.store:
            raise keyring.errors.PasswordDeleteError("missing")
        del self.store[(service, user)]


def test_token_goes_to_the_credential_store_never_to_a_file(monkeypatch, tmp_path):
    monkeypatch.setenv("RASPI_HOME", str(tmp_path))
    mem = MemoryKeyring()
    monkeypatch.setattr(cs, "keyring", mem)
    mem.delete_password = lambda s, u: (_ for _ in ()).throw(
        __import__("keyring").errors.PasswordDeleteError("x")
    )
    assert cs.get_token() is None
    cs.set_token("  " + TOKEN + "  ")
    assert cs.get_token() == TOKEN
    cs.save_settings(cs.CloudflareSettings(ACC.upper(), ZONE, "Example.COM"))
    assert all(TOKEN not in p.read_text() for p in tmp_path.rglob("*") if p.is_file())
    assert cs.load_settings() == cs.CloudflareSettings(ACC, ZONE, "example.com")
    cs.delete_token()  # deleting nothing must not raise
    with pytest.raises(cs.SecretStoreError):
        cs.set_token("   ")


def test_invalid_settings_are_rejected_before_writing(monkeypatch, tmp_path):
    monkeypatch.setenv("RASPI_HOME", str(tmp_path))
    with pytest.raises(RouteError):
        cs.save_settings(cs.CloudflareSettings("nope", ZONE, "example.com"))
    with pytest.raises(RouteError):
        cs.save_settings(cs.CloudflareSettings(ACC, ZONE, "nodot"))
    assert not (tmp_path / "cloudflare.yaml").exists()


def test_changing_the_service_of_an_existing_route_only_rewrites_the_rule():
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
    client = make(fake)
    plan = routing.plan_add_route(
        client, SETTINGS, "raspi-v", "game.example.com", "http://localhost:3000"
    )
    assert (
        len(plan.steps) == 1
        and "3000" in plan.steps[0]
        and not plan.create_tunnel
        and not plan.create_dns
    )
    routing.apply_add_route(client, SETTINGS, plan)
    writes = [(m, p.split("/")[-1]) for m, p, _ in fake.calls if m != "GET"]
    assert writes == [("PUT", "configurations")]
    assert fake.ingress["c" * 32][0]["service"] == "http://localhost:3000"
    assert fake.ingress["c" * 32][-1] == {"service": "http_status:404"}


@pytest.mark.parametrize(
    ("call", "expected"),
    [
        (lambda c: c.list_tunnels(ACC), "Cloudflare Tunnel › Edit"),
        (lambda c: c.list_zones("example.com"), "Zone › Zone › Read"),
        (lambda c: c.list_dns(ZONE, "x.example.com"), "Zone › DNS › Edit"),
    ],
)
def test_permission_errors_say_which_action_and_what_is_missing(call, expected):
    fake = FakeCloudflare(fail=(403, "Authentication error"))
    with pytest.raises(CloudflareError) as exc:
        call(make(fake))
    text = str(exc.value)
    assert expected in text and "HTTP 403" in text and "GET " in text
    assert (
        TOKEN not in text and ACC not in text
    )  # identifiers are shortened, token never present


UUID = "11111111-2222-3333-4444-555555555555"
OLD_TUNNEL = {"id": UUID, "name": "web-game", "config_src": "local", "status": "down"}


def test_tunnel_target_parsing():
    from raspi.core.routes import tunnel_id_from_target

    assert tunnel_id_from_target(f"{UUID}.cfargotunnel.com") == UUID
    assert tunnel_id_from_target(f"{UUID.upper()}.cfargotunnel.com") == UUID
    assert tunnel_id_from_target("example.net") is None
    assert tunnel_id_from_target(f"x{UUID}.cfargotunnel.com") is None


def test_dns_overview_lists_every_record_and_names_the_tunnel():
    fake = FakeCloudflare(
        [OLD_TUNNEL],
        {},
        [
            {
                "type": "CNAME",
                "name": "web-game.example.com",
                "content": f"{UUID}.cfargotunnel.com",
                "proxied": True,
            },
            {
                "type": "CNAME",
                "name": "gone.example.com",
                "content": "99999999-2222-3333-4444-555555555555.cfargotunnel.com",
                "proxied": True,
            },
            {
                "type": "A",
                "name": "a.example.com",
                "content": "1.2.3.4",
                "proxied": False,
            },
        ],
    )
    entries = routing.dns_overview(make(fake), SETTINGS)
    by_name = {e.name: e for e in entries}
    assert len(entries) == 3 and [e.name for e in entries] == sorted(by_name)
    assert (
        by_name["web-game.example.com"].note == "tunnel web-game"
        and by_name["web-game.example.com"].proxied
    )
    assert (
        "not found" in by_name["gone.example.com"].note
        and by_name["a.example.com"].note == ""
    )
    assert all(m == "GET" for m, _, _ in fake.calls)  # strictly read-only


def test_dns_listing_survives_a_tunnel_lookup_failure():
    class Flaky(FakeCloudflare):
        def handler(self, request):
            if request.url.path.endswith("/cfd_tunnel"):
                return httpx.Response(
                    403,
                    json={"success": False, "errors": [{"code": 1, "message": "no"}]},
                )
            return super().handler(request)

    fake = Flaky(
        dns=[
            {
                "type": "CNAME",
                "name": "x.example.com",
                "content": f"{UUID}.cfargotunnel.com",
                "proxied": True,
            }
        ]
    )
    entries = routing.dns_overview(make(fake), SETTINGS)
    assert len(entries) == 1 and "not found" in entries[0].note


def test_conflict_message_says_what_exists_and_what_to_do():
    fake = FakeCloudflare(
        [OLD_TUNNEL],
        {},
        [
            {
                "type": "CNAME",
                "name": "web-game.example.com",
                "content": f"{UUID}.cfargotunnel.com",
                "proxied": True,
            }
        ],
    )
    with pytest.raises(RouteError) as exc:
        routing.plan_add_route(
            make(fake),
            SETTINGS,
            "raspi-v",
            "web-game.example.com",
            "http://localhost:2567",
        )
    text = str(exc.value)
    assert f"{UUID}.cfargotunnel.com" in text and "tunnel web-game" in text
    assert "DNS records…" in text and "will not overwrite" in text


def test_list_all_dns_pages_until_a_short_page():
    pages = []

    def handler(request):
        page = int(request.url.params["page"])
        pages.append(page)
        count = 100 if page < 3 else 7
        return httpx.Response(
            200,
            json={
                "success": True,
                "result": [{"name": f"r{page}-{i}"} for i in range(count)],
            },
        )

    client = CloudflareClient(
        TOKEN, httpx.Client(transport=httpx.MockTransport(handler))
    )
    assert len(client.list_all_dns(ZONE)) == 207 and pages == [1, 2, 3]


def _old_cname_fake(old_status="down", extra_dns=None):
    old = {"id": UUID, "name": "web-game", "config_src": "local", "status": old_status}
    record = {
        "id": "rec1",
        "type": "CNAME",
        "name": "web-game.example.com",
        "content": f"{UUID}.cfargotunnel.com",
        "proxied": True,
        "ttl": 120,
        "comment": "game",
    }
    return FakeCloudflare([old], {}, [record] + (extra_dns or []))


def test_repoint_plan_shows_the_old_target_and_changes_nothing():
    fake = _old_cname_fake()
    client = make(fake)
    with pytest.raises(routing.DnsConflictError) as exc:
        routing.plan_add_route(
            client, SETTINGS, "raspi-v", "web-game.example.com", "http://localhost:2567"
        )
    assert exc.value.repointable is True
    plan = routing.plan_add_route(
        client,
        SETTINGS,
        "raspi-v",
        "web-game.example.com",
        "http://localhost:2567",
        repoint=True,
    )
    text = " ".join(plan.steps)
    assert (
        f"{UUID}.cfargotunnel.com" in text
        and "tunnel 'web-game', status down" in text
        and "WARNING" not in text
    )
    assert (
        plan.repoint_record["id"] == "rec1"
        and plan.create_tunnel
        and not plan.create_dns
    )
    assert all(m == "GET" for m, _, _ in fake.calls)


def test_repoint_warns_when_the_old_tunnel_is_in_use():
    plan = routing.plan_add_route(
        make(_old_cname_fake("healthy")),
        SETTINGS,
        "raspi-v",
        "web-game.example.com",
        "http://localhost:2567",
        repoint=True,
    )
    assert "WARNING" in " ".join(plan.steps) and "stop working" in " ".join(plan.steps)


def test_apply_repoint_patches_once_keeping_ttl_and_comment_after_the_tunnel_exists():
    fake = _old_cname_fake()
    client = make(fake)
    plan = routing.plan_add_route(
        client,
        SETTINGS,
        "raspi-v",
        "web-game.example.com",
        "http://localhost:2567",
        repoint=True,
    )
    result = routing.apply_add_route(client, SETTINGS, plan)
    writes = [(m, p.split("/")[-1]) for m, p, _ in fake.calls if m != "GET"]
    assert writes == [
        ("POST", "cfd_tunnel"),
        ("PUT", "configurations"),
        ("PATCH", "rec1"),
    ]
    record = fake.dns[0]
    assert (
        record["content"] == f"{'c' * 32}.cfargotunnel.com"
        and record["proxied"] is True
    )
    assert (
        record["ttl"] == 120
        and record["comment"] == "game"
        and record["type"] == "CNAME"
    )
    assert any("previous target" in m and UUID in m for m in result.messages)


@pytest.mark.parametrize(
    "records",
    [
        [
            {
                "id": "a1",
                "type": "A",
                "name": "web-game.example.com",
                "content": "1.2.3.4",
            }
        ],
        [
            {
                "id": "c1",
                "type": "CNAME",
                "name": "web-game.example.com",
                "content": "x.example.net",
            },
            {
                "id": "t1",
                "type": "TXT",
                "name": "web-game.example.com",
                "content": "hello",
            },
        ],
    ],
)
def test_only_a_single_cname_can_be_repointed(records):
    fake = FakeCloudflare([], {}, records)
    for repoint in (False, True):
        with pytest.raises(routing.DnsConflictError) as exc:
            routing.plan_add_route(
                make(fake),
                SETTINGS,
                "raspi-v",
                "web-game.example.com",
                "http://localhost:2567",
                repoint=repoint,
            )
        assert exc.value.repointable is False


# --- one tunnel per project -------------------------------------------------------------
HOSTS = ["raspi", "raspi-v"]


def test_tunnel_names_with_and_without_project():
    assert routing.tunnel_name_for("raspi-v") == "raspi-manager-raspi-v"
    assert (
        routing.tunnel_name_for("raspi-v", "web-game")
        == "raspi-manager-raspi-v--web-game"
    )
    assert routing.tunnel_name_for("raspi-v", "") == "raspi-manager-raspi-v"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("raspi-manager-raspi", ("raspi", None)),
        ("raspi-manager-raspi-v", ("raspi-v", None)),  # not host "raspi" + project "v"
        ("raspi-manager-raspi-v--web-game", ("raspi-v", "web-game")),
        ("raspi-manager-raspi--v-game", ("raspi", "v-game")),
        ("raspi-manager-raspi-v--", None),
        ("raspi-manager-other", None),
        ("web-game", None),
    ],
)
def test_parse_tunnel_name_is_unambiguous_for_hyphenated_pis(name, expected):
    assert routing.parse_tunnel_name(name, HOSTS) == expected


def test_each_project_gets_its_own_tunnel_on_the_same_pi():
    fake = FakeCloudflare()
    client = make(fake)
    shared = routing.plan_add_route(
        client, SETTINGS, "raspi-v", "a.example.com", "http://localhost:1"
    )
    game = routing.plan_add_route(
        client,
        SETTINGS,
        "raspi-v",
        "game.example.com",
        "http://localhost:2",
        project="web-game",
    )
    assert shared.tunnel_name == "raspi-manager-raspi-v"
    assert game.tunnel_name == "raspi-manager-raspi-v--web-game" and game.create_tunnel
    assert "raspi-manager-raspi-v--web-game" in game.steps[0]


def test_existing_project_tunnel_is_reused_not_duplicated():
    tunnel = {
        "id": "c" * 32,
        "name": "raspi-manager-raspi-v--web-game",
        "config_src": "cloudflare",
    }
    fake = FakeCloudflare([tunnel], {"c" * 32: [{"service": "http_status:404"}]})
    plan = routing.plan_add_route(
        make(fake),
        SETTINGS,
        "raspi-v",
        "game.example.com",
        "http://localhost:2",
        project="web-game",
    )
    assert not plan.create_tunnel and plan.tunnel_id == "c" * 32


@pytest.mark.parametrize("bad", ["a b", "../x", "x;rm", "-x"])
def test_invalid_project_names_are_rejected(bad):
    with pytest.raises(RouteError, match="Invalid project"):
        routing.plan_add_route(
            make(FakeCloudflare()),
            SETTINGS,
            "raspi-v",
            "game.example.com",
            "http://localhost:2",
            project=bad,
        )


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
    plan = routing.plan_delete_tunnel(make(fake), SETTINGS, TID, HOSTS)
    assert plan.name == "raspi-manager-raspi-v--web-game" and plan.status == "inactive"
    assert [r.hostname for r in plan.routes] == ["game.example.com"]
    assert plan.dns_hostnames == ["game.example.com"]
    assert all(m == "GET" for m, _, _ in fake.calls)


@pytest.mark.parametrize("status", ["healthy", "degraded"])
def test_delete_is_refused_while_a_connector_is_active(status):
    with pytest.raises(RouteError, match="connector is still running"):
        routing.plan_delete_tunnel(make(_delete_fake(status)), SETTINGS, TID, HOSTS)


def test_delete_is_refused_for_foreign_or_missing_tunnels():
    with pytest.raises(RouteError, match="not created by raspi-manager"):
        routing.plan_delete_tunnel(
            make(_delete_fake(name="web-game")), SETTINGS, TID, HOSTS
        )
    with pytest.raises(RouteError, match="not created by raspi-manager"):
        routing.plan_delete_tunnel(
            make(_delete_fake(name="raspi-manager-unknownpi")), SETTINGS, TID, HOSTS
        )
    with pytest.raises(RouteError, match="no longer exists"):
        routing.plan_delete_tunnel(make(FakeCloudflare()), SETTINGS, TID, HOSTS)


def test_apply_deletes_once_reports_dangling_dns_and_never_touches_dns():
    fake = _delete_fake("down")
    client = make(fake)
    plan = routing.plan_delete_tunnel(client, SETTINGS, TID, HOSTS)
    message = routing.apply_delete_tunnel(client, SETTINGS, plan)
    writes = [(m, p.split("/")[-1]) for m, p, _ in fake.calls if m != "GET"]
    assert writes == [("DELETE", TID)] and fake.tunnels == []
    assert "game.example.com" in message and "should be removed or repointed" in message
    assert not any("dns_records" in p and m != "GET" for m, p, _ in fake.calls)


def test_apply_rechecks_the_status_so_a_connector_started_after_the_preview_blocks_the_delete():
    fake = _delete_fake("inactive")
    client = make(fake)
    plan = routing.plan_delete_tunnel(client, SETTINGS, TID, HOSTS)
    fake.tunnels[0]["status"] = "healthy"  # a connector came up in between
    with pytest.raises(RouteError, match="became healthy"):
        routing.apply_delete_tunnel(client, SETTINGS, plan)
    assert not any(m == "DELETE" for m, _, _ in fake.calls)
