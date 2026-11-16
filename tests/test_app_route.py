import base64
import re

import httpx
import pytest

from raspi.cloudflare.client import CloudflareError
from raspi.cloudflare.release import fetch_download
from raspi.config.cloudflare_settings import CloudflareSettings
from raspi.core.connector import (
    HIDDEN,
    ConnectorError,
    Download,
    asset_for,
    build_check_script,
    build_install_script,
    build_ports_script,
    parse_check,
    parse_ports,
    parse_release_checksum,
    render_unit,
    unit_name_for,
)
from raspi.core.models import AppRef, Host
from raspi.core.routes import RouteError
from raspi.services import app_route
from raspi.ssh.transport import RunResult

SECRET = "eyJhIjoiU0VDUkVULVRPS0VOIn0="
ZONE, ACCOUNT = "z" * 32, "a" * 32
SETTINGS = CloudflareSettings(ACCOUNT, ZONE, "example.com")
HOST = Host("raspi-v", "10.0.0.2", "pi")
APP = AppRef("web-game", "web-game.service", "/home/pi/dev/web-game")
DOWNLOAD = Download("2026.11.0", "cloudflared-linux-arm64", "ab" * 32)

NOTES = (
    "### SHA256 Checksums:\n```\n"
    f"cloudflared-linux-arm64: {'ab' * 32}\n"
    f"cloudflared-linux-armhf: {'cd' * 32}\n```\n"
)


class FakeCloudflare:
    def __init__(self, healthy=True, token_error=False):
        self.tunnels, self.ingress, self.dns = {}, {}, {}
        self.healthy, self.token_error, self.calls = healthy, token_error, []

    def list_tunnels(self, account, name=None):
        found = [t for t in self.tunnels.values() if name in (None, t["name"])]
        return [
            {**t, "status": "healthy" if self.healthy else "inactive"} for t in found
        ]

    def create_tunnel(self, account, name):
        tid = f"{len(self.tunnels) + 1:032x}"
        self.tunnels[tid] = {"id": tid, "name": name, "config_src": "cloudflare"}
        self.calls.append("create_tunnel")
        return {"id": tid, "token": "must-not-be-kept"}

    def get_ingress(self, account, tid):
        return self.ingress.get(tid, [{"service": "http_status:404"}])

    def put_ingress(self, account, tid, ingress):
        self.ingress[tid] = ingress
        self.calls.append("put_ingress")

    def list_dns(self, zone, name):
        return [r for r in self.dns.values() if r["name"] == name]

    def create_cname(self, zone, name, target):
        self.dns[name] = {"id": "r1", "type": "CNAME", "name": name, "content": target}
        self.calls.append("create_cname")

    def tunnel_token(self, account, tid):
        if self.token_error:
            raise CloudflareError("token refused")
        return SECRET


CHECK_FRESH = "machine=aarch64\nbin=\nversion=\ncurl=yes\nsudo=yes\nload=not-found\nactive=inactive\n"
CHECK_HAS_BIN = CHECK_FRESH.replace("bin=\n", "bin=/usr/bin/cloudflared\n")
CHECK_RUNNING = CHECK_HAS_BIN.replace("load=not-found", "load=loaded").replace(
    "active=inactive", "active=active"
)


class Pi:
    def __init__(self, check=CHECK_FRESH, ports="", install_error=""):
        self.check, self.ports, self.install_error = check, ports, install_error
        self.installs = []

    def run_script(self, host, script, timeout=20.0):
        if "TUNNEL_TOKEN" in script:
            self.installs.append(script)
            if self.install_error:
                return RunResult(False, "", self.install_error)
            return RunResult(True, "installed\n")
        if "machine=" in script or "uname -m" in script:
            return RunResult(True, self.check)
        return RunResult(True, self.ports)


def lookup(machine):
    return DOWNLOAD


def plan(client=None, pi=None, **kw):
    return app_route.plan_app_route(
        client or FakeCloudflare(),
        SETTINGS,
        HOST,
        APP,
        kw.pop("hostname", "game.example.com"),
        kw.pop("port", 2567),
        pi or Pi(),
        lookup=kw.pop("lookup", lookup),
        **kw,
    )


# --- pure helpers ------------------------------------------------------------------
def test_assets_follow_the_pi_architecture():
    assert asset_for("aarch64") == "cloudflared-linux-arm64"
    assert asset_for("armv7l") == "cloudflared-linux-armhf"
    with pytest.raises(ConnectorError, match="architecture"):
        asset_for("riscv64")


def test_release_checksum_is_read_from_the_notes():
    assert parse_release_checksum(NOTES, "cloudflared-linux-armhf") == "cd" * 32
    assert parse_release_checksum(NOTES, "cloudflared-linux-arm") is None


def test_unit_name_and_text_keep_the_token_out():
    unit = unit_name_for("raspi-manager-raspi-v--web-game")
    assert unit == "raspi-tunnel-raspi-v--web-game.service"
    text = render_unit("raspi-manager-raspi-v--web-game", unit, "/usr/bin/cloudflared")
    assert "EnvironmentFile=/etc/cloudflared/raspi-tunnel-raspi-v--web-game.env" in text
    assert "ExecStart=/usr/bin/cloudflared --no-autoupdate tunnel run" in text
    assert "TUNNEL_TOKEN" not in text and "--token" not in text
    with pytest.raises(ConnectorError):
        unit_name_for("raspi-manager-bad name;rm")


def test_install_script_is_private_checked_and_hides_the_preview_token():
    unit = "raspi-tunnel-x.service"
    unit_text = render_unit("raspi-manager-x", unit, "/usr/local/bin/cloudflared")
    real = build_install_script(unit, unit_text, SECRET, DOWNLOAD)
    shown = build_install_script(unit, unit_text, HIDDEN, DOWNLOAD)
    assert SECRET in real and SECRET not in shown and HIDDEN in shown
    for needed in (
        "sha256sum -c",
        "checksum mismatch",
        "sudo chmod 600",
        "umask 077",
        "systemctl restart",
        "passwordless sudo",
    ):
        assert needed in real
    assert DOWNLOAD.url in real and "--token" not in real
    encoded = re.search(r"printf %s (\S+) \| base64 -d", real)[1]
    assert base64.b64decode(encoded).decode() == unit_text
    no_download = build_install_script(unit, unit_text, SECRET, None)
    assert "curl" not in no_download and "sha256sum" not in no_download


def test_check_and_ports_parsers():
    state = parse_check(CHECK_RUNNING)
    assert state.machine == "aarch64" and state.bin_path == "/usr/bin/cloudflared"
    assert state.has_sudo and state.has_curl and state.unit_loaded
    assert state.unit_active == "active"
    assert not parse_check(CHECK_FRESH).unit_loaded
    ss = (
        'LISTEN 0 511 *:3000 *:* users:(("node",pid=5,fd=19))\n'
        'LISTEN 0 4096 127.0.0.1:2567 0.0.0.0:* users:(("node",pid=5,fd=20))\n'
        'LISTEN 0 128 [::]:3000 [::]:* users:(("node",pid=6,fd=3))\n'
    )
    assert parse_ports(ss) == [2567, 3000]
    check = build_check_script("a.service")
    assert "sudo -n true" in check  # only asks whether sudo works without a password
    for forbidden in ("tee", "install", "systemctl enable", "rm ", ">>"):
        assert forbidden not in check
    assert "sudo" not in build_ports_script("a.service")


def test_fetch_download_reads_the_public_release():
    payload = {
        "tag_name": "2026.11.0",
        "body": NOTES,
        "assets": [{"name": "cloudflared-linux-arm64"}],
    }
    seen = []

    def handler(request):
        seen.append(request.headers["user-agent"])
        return httpx.Response(200, json=payload)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    got = fetch_download("aarch64", client)
    assert got == Download("2026.11.0", "cloudflared-linux-arm64", "ab" * 32)
    assert seen == ["raspi-manager"]  # generic name, nothing about the user
    with pytest.raises(ConnectorError, match="no file named"):
        fetch_download("armv7l", client)


def test_fetch_download_refuses_a_release_without_checksum():
    payload = {
        "tag_name": "t",
        "body": "no sums",
        "assets": [{"name": "cloudflared-linux-arm64"}],
    }
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
    )
    with pytest.raises(ConnectorError, match="install refused"):
        fetch_download("aarch64", client)
    broken = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(ConnectorError, match="Could not read"):
        fetch_download("aarch64", broken)


# --- planning ------------------------------------------------------------------------
def test_ports_are_detected_read_only():
    pi = Pi(ports='LISTEN 0 511 *:2567 *:* users:(("node",pid=5,fd=19))\n')
    assert app_route.detect_ports(HOST, APP, pi) == [2567]
    assert app_route.detect_ports(HOST, APP, Pi(ports="")) == []


@pytest.mark.parametrize("bad", [0, 70000, "abc", ""])
def test_port_is_validated(bad):
    with pytest.raises(RouteError):
        app_route.check_port(bad)


def test_plan_covers_cloudflare_and_the_pi_without_changing_anything():
    cf, pi = FakeCloudflare(), Pi()
    result = plan(cf, pi)
    assert cf.calls == [] and pi.installs == []
    assert result.cloudflare.tunnel_name == "raspi-manager-raspi-v--web-game"
    assert result.install and result.download == DOWNLOAD
    text = "\n".join(result.steps)
    assert "Create the tunnel" in text and "http://localhost:2567" in text
    assert "Download cloudflared 2026.11.0" in text and "On raspi-v:" in text
    assert SECRET not in result.preview_script() and HIDDEN in result.preview_script()


def test_plan_reuses_the_cloudflared_already_on_the_pi():
    result = plan(pi=Pi(CHECK_HAS_BIN))
    assert result.download is None and result.install
    encoded = re.search(r"printf %s (\S+) \| base64 -d", result.preview_script())[1]
    assert "ExecStart=/usr/bin/cloudflared" in base64.b64decode(encoded).decode()


def existing_tunnel(healthy=True):
    """A Cloudflare that already has this app's tunnel (so it is not created again)."""
    cf = FakeCloudflare(healthy=healthy)
    tid = "9" * 32
    cf.tunnels[tid] = {
        "id": tid,
        "name": "raspi-manager-raspi-v--web-game",
        "config_src": "cloudflare",
    }
    return cf


def test_plan_does_nothing_on_the_pi_when_the_connector_runs_and_its_tunnel_is_connected():
    result = plan(existing_tunnel(), Pi(CHECK_RUNNING))
    assert not result.install and result.pi_steps == [] and result.replace_reason == ""


def test_a_running_connector_is_not_trusted_when_the_tunnel_is_new_or_not_connected():
    """cloudflared stays 'active' while it retries with a token whose tunnel was deleted."""
    fresh = plan(FakeCloudflare(), Pi(CHECK_RUNNING))  # the tunnel has to be created
    assert fresh.install and "deleted" in fresh.replace_reason
    assert any("Replace the token" in s for s in fresh.pi_steps)
    assert any("restart" in s for s in fresh.pi_steps)
    down = plan(existing_tunnel(healthy=False), Pi(CHECK_RUNNING))
    assert down.install and "inactive" in down.replace_reason
    assert "systemctl restart" in down.preview_script()


def test_a_cloudflare_lookup_failure_keeps_what_runs():
    from types import SimpleNamespace

    class Flaky:
        def list_tunnels(self, account, name=None):
            raise CloudflareError("rate limited")

    existing = SimpleNamespace(create_tunnel=False, tunnel_name="raspi-manager-x--y")
    assert (
        app_route._stale_reason(Flaky(), SETTINGS, existing) == ""
    )  # cannot tell: keep
    assert "deleted" in app_route._stale_reason(
        Flaky(), SETTINGS, SimpleNamespace(create_tunnel=True, tunnel_name="x")
    )


@pytest.mark.parametrize(
    "check, message",
    [
        (CHECK_FRESH.replace("sudo=yes", "sudo=no"), "passwordless sudo"),
        (CHECK_FRESH.replace("curl=yes", "curl=no"), "both missing"),
        (CHECK_FRESH.replace("aarch64", "riscv64"), "architecture"),
    ],
)
def test_plan_refuses_a_pi_that_cannot_run_the_connector(check, message):
    def strict(machine):
        return Download("t", asset_for(machine), "ab" * 32)

    with pytest.raises(ConnectorError, match=message):
        plan(pi=Pi(check), lookup=strict)


def test_plan_rejects_a_hostname_outside_the_zone():
    with pytest.raises(RouteError, match="not inside the zone"):
        plan(hostname="game.other.org")


# --- applying -------------------------------------------------------------------------
def run(cf, pi, **kw):
    p = plan(cf, pi)
    return p, app_route.apply_app_route(
        cf,
        SETTINGS,
        p,
        pi,
        sleep=lambda s: None,
        http_get=kw.get("http", lambda u: 200),
    )


def test_apply_creates_cloudflare_side_then_installs_and_verifies():
    cf, pi = FakeCloudflare(), Pi()
    _, result = run(cf, pi)
    assert result.ok and cf.calls == ["create_tunnel", "put_ingress", "create_cname"]
    (script,) = pi.installs
    assert SECRET in script  # only inside the script sent to the Pi
    joined = "\n".join(result.messages)
    assert SECRET not in joined and "must-not-be-kept" not in joined
    assert "installed and started on raspi-v" in joined
    assert "is healthy" in joined and "answered 200" in joined


def test_apply_skips_the_pi_when_the_connector_runs_and_is_connected():
    cf, pi = existing_tunnel(), Pi(CHECK_RUNNING)
    _, result = run(cf, pi)
    assert result.ok and pi.installs == []


def test_apply_replaces_the_token_of_a_running_connector_whose_tunnel_was_recreated():
    cf, pi = FakeCloudflare(), Pi(CHECK_RUNNING)
    _, result = run(cf, pi)
    assert result.ok and len(pi.installs) == 1
    assert "systemctl restart" in pi.installs[0]
    assert SECRET not in " ".join(result.messages)


def test_a_failed_install_is_reported_and_never_shows_the_token():
    cf, pi = FakeCloudflare(), Pi(install_error=f"boom {SECRET} boom")
    _, result = run(cf, pi)
    assert not result.ok
    joined = "\n".join(result.messages)
    assert "failed" in joined and SECRET not in joined and HIDDEN in joined


def test_token_failure_stops_before_touching_the_pi():
    cf, pi = FakeCloudflare(token_error=True), Pi()
    _, result = run(cf, pi)
    assert not result.ok and pi.installs == []
    assert "connector token" in "\n".join(result.messages)


def test_unhealthy_tunnel_and_unanswered_url_are_reported_not_fatal():
    def refused(url):
        raise httpx.ConnectError("no route")

    cf, pi = FakeCloudflare(healthy=False), Pi()
    _, result = run(cf, pi, http=refused)
    joined = "\n".join(result.messages)
    assert result.ok and "is inactive: check again" in joined
    assert "did not answer yet (ConnectError)" in joined


def test_a_bad_gateway_hints_at_the_port():
    cf, pi = FakeCloudflare(), Pi()
    _, result = run(cf, pi, http=lambda u: 502)
    assert "does not answer on that port" in "\n".join(result.messages)


def test_a_second_run_changes_nothing():
    cf, pi = FakeCloudflare(), Pi()
    run(cf, pi)
    again = plan(cf, Pi(CHECK_RUNNING))
    assert again.nothing_to_do


def test_ports_script_never_fails_just_because_nothing_matched():
    script = build_ports_script("a.service")
    assert "|| true" in script and script.rstrip().endswith("exit 0")
