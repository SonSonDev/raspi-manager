import pytest
from test_cloudflare import ACC, SETTINGS, TOKEN, ZONE, FakeCloudflare, make

from raspi.cloudflare.client import CloudflareError
from raspi.config import cloudflare_settings as cs
from raspi.core.routes import (
    RouteError,
)
from raspi.services import routing


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
