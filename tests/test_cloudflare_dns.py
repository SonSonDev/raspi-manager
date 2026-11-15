import httpx
import pytest
from test_cloudflare import HOSTS, SETTINGS, TOKEN, ZONE, FakeCloudflare, make

from raspi.cloudflare.client import CloudflareClient
from raspi.core.routes import (
    RouteError,
)
from raspi.services import routing

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
