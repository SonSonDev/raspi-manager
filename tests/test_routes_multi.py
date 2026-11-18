import base64
import re
import subprocess

import pytest
import yaml

from raspi.config.loader import ConfigError, check_route, parse_manifest
from raspi.core.manifest import render_manifest
from raspi.core.models import AppRef, AppRoute, AppStatus, Host
from raspi.core.remote import build_status_script, parse_status
from raspi.core.route_health import down_routes, route_summary, route_warnings
from raspi.core.stage import READY, next_step
from raspi.services.declare import record_route
from raspi.ssh.transport import RunResult


class Pi:
    """A Pi holding apps.yaml: answers reads and applies base64 writes."""

    def __init__(self, apps):
        self.text = None if apps is None else render_manifest(apps)

    def run_script(self, host, script, timeout=20.0):
        if "base64 -d" in script:
            self.text = base64.b64decode(
                re.search(r"printf %s '?([A-Za-z0-9+/=]+)'? \|", script)[1]
            ).decode()
            return RunResult(True, "written\n")
        if self.text is None:
            return RunResult(True, "@@MISSING\n")
        return RunResult(True, "@@EXISTS\n" + self.text)

    def apps(self):
        return parse_manifest(yaml.safe_load(self.text))


MAIN = AppRoute("fill.example.com", 8787, "/robots.txt")
SHARE = AppRoute("share.example.com", 8788, "/robots.txt")
APP = AppRef(
    "fill", "fill.service", repo="me/fill", run="npm start", routes=(MAIN, SHARE)
)


def status(codes, **kw) -> AppStatus:
    base = {
        "name": "fill",
        "unit": "fill.service",
        "load": "loaded",
        "active": "active",
        "rev": "abc1234",
        "key_present": True,
    }
    return AppStatus(**base, route_codes=codes, **kw)


def test_routes_round_trip_through_apps_yaml():
    again = parse_manifest(yaml.safe_load(render_manifest((APP,))))
    assert again[0].routes == (MAIN, SHARE)


@pytest.mark.parametrize(
    "entry",
    [
        {"hostname": "nodot", "port": 80},
        {"hostname": "a.example.com", "port": 0},
        {"hostname": "a.example.com", "port": 70000},
        {"hostname": "a.example.com", "port": "x"},
        {"hostname": "a.example.com", "port": 80, "check": "robots"},
        {"hostname": "a.example.com", "port": 80, "check": "/a/../b"},
        {"hostname": "a.example.com", "port": 80, "check": "/a?token=1"},
    ],
)
def test_invalid_routes_are_refused(entry):
    doc = {
        "version": 1,
        "apps": [{"name": "a", "unit": "a.service", "routes": [entry]}],
    }
    with pytest.raises(ConfigError):
        parse_manifest(doc)


def test_the_same_hostname_twice_in_one_app_is_refused():
    route = {"hostname": "a.example.com", "port": 80}
    doc = {
        "version": 1,
        "apps": [{"name": "a", "unit": "a.service", "routes": [route, route]}],
    }
    with pytest.raises(ConfigError, match="Duplicate route"):
        parse_manifest(doc)


def test_check_route_normalises_and_defaults():
    assert check_route("Share.Example.com", "8788") == AppRoute(
        "share.example.com", 8788, "/"
    )


def test_status_script_checks_each_route_locally_and_parses_codes():
    script = build_status_script((APP,))
    assert "http://127.0.0.1:8787/robots.txt" in script
    assert "http://127.0.0.1:8788/robots.txt" in script
    assert (
        subprocess.run(["sh", "-n"], input=script.encode(), check=False).returncode == 0
    )
    out = "@@APP fill\nLoadState=loaded\nroute0=200\nroute1=000\n"
    assert parse_status(out, (APP,))[0].route_codes == (200, 0)


def test_main_up_share_down():
    st = status((200, 0))
    assert route_summary(APP, st) == (
        "fill.example.com :8787 up, share.example.com :8788 down"
    )
    assert down_routes(APP, st) == [SHARE]
    nxt = next_step(APP, st)
    assert nxt.label == "Route not answering" and "share.example.com" in nxt.why


def test_all_routes_up_is_ready_and_a_404_still_counts_as_answering():
    nxt = next_step(APP, status((200, 404)))
    assert nxt.label == READY and nxt.done


def test_no_route_means_no_extra_state():
    plain = AppRef("p", "p.service", repo="me/p", run="x")
    st = AppStatus("p", "p.service", load="loaded", active="active", rev="")
    assert route_summary(plain, st) == ""


def test_overlaps_with_other_apps_are_warnings():
    other = AppRef(
        "other",
        "o.service",
        routes=(AppRoute("fill.example.com", 9000), AppRoute("x.example.com", 8788)),
    )
    host = Host("pi", "10.0.0.1", "u", (APP, other))
    lines = route_warnings(host, APP, AppRoute("fill.example.com", 8788))
    assert any("already a route of other" in line for line in lines)
    assert any("Port 8788 is also the port of other" in line for line in lines)


def test_record_route_adds_then_replaces_by_hostname():
    pi = Pi((AppRef("fill", "fill.service"),))
    host = Host("pi", "10.0.0.1", "u")
    assert record_route(host, "fill", "fill.example.com", 8787, "/robots.txt", pi).ok
    assert record_route(host, "fill", "share.example.com", 8788, "/", pi).ok
    assert record_route(host, "fill", "fill.example.com", 9000, "/", pi).ok
    routes = pi.apps()[0].routes
    assert [(r.hostname, r.port) for r in routes] == [
        ("fill.example.com", 9000),
        ("share.example.com", 8788),
    ]


def test_record_route_without_inventory_or_with_bad_input_changes_nothing():
    host = Host("pi", "10.0.0.1", "u")
    pi = Pi(None)
    assert not record_route(host, "fill", "fill.example.com", 80, "/", pi).ok
    pi = Pi((AppRef("fill", "fill.service"),))
    assert not record_route(host, "fill", "bad", 80, "/", pi).ok
    assert not pi.apps()[0].routes
