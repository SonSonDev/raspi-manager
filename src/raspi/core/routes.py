"""Pure logic for tunnel routes (ingress rules). No I/O."""

from __future__ import annotations

import re
from dataclasses import dataclass

CATCH_ALL = {"service": "http_status:404"}
_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,}$"
)
_SERVICE = re.compile(r"^https?://(localhost|127\.0\.0\.1|\[::1\]):\d{1,5}(/.*)?$")
_ID = re.compile(r"^[0-9a-f]{32}$")


class RouteError(ValueError):
    """Invalid hostname, service or identifier (message is safe to display)."""


@dataclass(frozen=True)
class Route:
    hostname: str
    service: str


def check_hostname(value: str) -> str:
    value = value.strip().lower()
    if not _HOSTNAME.match(value):
        raise RouteError(
            f"Invalid hostname: {value!r} (expected something like app.example.com)"
        )
    return value


def check_service(value: str) -> str:
    """Only services on the Pi itself are allowed (the connector runs there)."""
    value = value.strip()
    if not _SERVICE.match(value):
        raise RouteError(
            "Service must look like http://localhost:2567 (a port on this Pi)"
        )
    return value


def check_cf_id(value: str, what: str) -> str:
    value = value.strip().lower()
    if not _ID.match(value):
        raise RouteError(f"Invalid {what}: expected 32 hexadecimal characters")
    return value


def routes_of(ingress: list[dict]) -> list[Route]:
    return [Route(r["hostname"], r["service"]) for r in ingress if r.get("hostname")]


def with_route(ingress: list[dict], route: Route) -> list[dict]:
    """Ingress with `route` added (or updated); the catch-all rule is kept last."""
    rules = [
        dict(r)
        for r in ingress
        if r.get("hostname") and r["hostname"] != route.hostname
    ]
    rules.append({"hostname": route.hostname, "service": route.service})
    rules.sort(key=lambda r: r["hostname"])
    return rules + [dict(CATCH_ALL)]


def without_route(ingress: list[dict], hostname: str) -> list[dict]:
    rules = [
        dict(r) for r in ingress if r.get("hostname") and r["hostname"] != hostname
    ]
    return rules + [dict(CATCH_ALL)]


_TUNNEL_TARGET = re.compile(
    r"^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.cfargotunnel\.com$"
)


def tunnel_id_from_target(content: str) -> str | None:
    """Tunnel UUID if a CNAME target is `<uuid>.cfargotunnel.com`, else None."""
    m = _TUNNEL_TARGET.match(content.strip().lower())
    return m.group(1) if m else None


def zone_of(hostname: str, zone_name: str) -> bool:
    return hostname == zone_name or hostname.endswith("." + zone_name)
