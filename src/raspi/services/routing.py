"""Tunnel + route management through the Cloudflare API: overview, plan (read-only), apply."""

from __future__ import annotations

from dataclasses import dataclass, field

from raspi.cloudflare.client import CloudflareClient, CloudflareError
from raspi.config.cloudflare_settings import CloudflareSettings
from raspi.config.loader import ConfigError, check_label
from raspi.core.routes import (
    Route,
    RouteError,
    check_hostname,
    check_service,
    routes_of,
    tunnel_id_from_target,
    with_route,
    without_route,
    zone_of,
)

TUNNEL_PREFIX = "raspi-manager-"
PROJECT_SEP = "--"


def tunnel_name_for(host_name: str, project: str | None = None) -> str:
    """`raspi-manager-<pi>` (shared by all routes of the Pi) or `raspi-manager-<pi>--<project>`."""
    base = f"{TUNNEL_PREFIX}{host_name}"
    return f"{base}{PROJECT_SEP}{project}" if project else base


def _project_label(project: str | None) -> str | None:
    if not project or not project.strip():
        return None
    try:
        return check_label(project.strip())
    except ConfigError as exc:
        raise RouteError(
            f"Invalid project name: {project!r} (letters, digits, . _ -)"
        ) from exc


def parse_tunnel_name(
    name: str, host_names: list[str]
) -> tuple[str, str | None] | None:
    """(pi, project) for a tunnel created by raspi-manager for one of `host_names`, else None.

    Matching against the known Pis keeps hyphenated names unambiguous (raspi vs raspi-v)."""
    if not name.startswith(TUNNEL_PREFIX):
        return None
    rest = name[len(TUNNEL_PREFIX) :]
    for host in sorted(host_names, key=len, reverse=True):
        if rest == host:
            return host, None
        if rest.startswith(host + PROJECT_SEP) and len(rest) > len(host) + len(
            PROJECT_SEP
        ):
            return host, rest[len(host) + len(PROJECT_SEP) :]
    return None


@dataclass
class TunnelInfo:
    id: str
    name: str
    status: str
    routes: list[Route] = field(default_factory=list)


def overview(
    client: CloudflareClient, settings: CloudflareSettings
) -> list[TunnelInfo]:
    """All live tunnels of the account with their routes (read-only)."""
    tunnels = []
    for t in client.list_tunnels(settings.account_id):
        routes = (
            routes_of(client.get_ingress(settings.account_id, t["id"]))
            if t.get("config_src") == "cloudflare"
            else []
        )
        tunnels.append(
            TunnelInfo(t["id"], t["name"], t.get("status", "unknown"), routes)
        )
    return sorted(tunnels, key=lambda t: t.name)


class DnsConflictError(RouteError):
    """The hostname already has a DNS record. `repointable` is True for a single CNAME."""

    def __init__(self, message: str, repointable: bool) -> None:
        super().__init__(message)
        self.repointable = repointable


@dataclass
class RoutePlan:
    tunnel_name: str
    route: Route
    steps: list[str]  # human-readable, exact changes
    create_tunnel: bool
    tunnel_id: str = ""
    ingress: list[dict] = field(
        default_factory=list
    )  # the full list that will be written
    create_dns: bool = False
    repoint_record: dict | None = None  # existing CNAME to point at the new tunnel

    @property
    def nothing_to_do(self) -> bool:
        return not self.steps


def plan_add_route(
    client: CloudflareClient,
    settings: CloudflareSettings,
    host_name: str,
    hostname: str,
    service: str,
    repoint: bool = False,
    project: str | None = None,
) -> RoutePlan:
    """Read-only: what would change. Raises RouteError / CloudflareError for conflicts.

    With `repoint=True`, a single existing CNAME is planned to be repointed instead of refused."""
    hostname, service = check_hostname(hostname), check_service(service)
    if not settings.complete:
        raise RouteError("Set the account ID and the zone first")
    if not zone_of(hostname, settings.zone_name):
        raise RouteError(f"{hostname} is not inside the zone {settings.zone_name}")
    route = Route(hostname, service)
    name = tunnel_name_for(host_name, _project_label(project))
    existing = client.list_tunnels(settings.account_id, name)
    steps: list[str] = []
    tunnel_id, ingress = "", []
    if existing:
        tunnel_id = existing[0]["id"]
        if existing[0].get("config_src") != "cloudflare":
            raise RouteError(
                f"Tunnel '{name}' is managed locally on the Pi, not by Cloudflare: raspi-manager will not edit it"
            )
        ingress = client.get_ingress(settings.account_id, tunnel_id)
    else:
        steps.append(f"Create the tunnel '{name}' (managed by Cloudflare)")
    new_ingress = with_route(ingress, route)
    if route not in routes_of(ingress):
        steps.append(f"Route {hostname} to {service} in tunnel '{name}'")
    target = (
        f"{tunnel_id}.cfargotunnel.com"
        if tunnel_id
        else "<new tunnel>.cfargotunnel.com"
    )
    records = client.list_dns(settings.zone_id, hostname)
    create_dns, repoint_record = False, None
    if not records:
        create_dns = True
        steps.append(f"Create the proxied DNS record {hostname} → {target}")
    else:
        cname = next((r for r in records if r.get("type") == "CNAME"), None)
        if not cname or not tunnel_id or cname.get("content") != target:
            repointable = cname is not None and len(records) == 1
            if not (repoint and repointable):
                raise DnsConflictError(
                    _conflict_message(client, settings, hostname, records), repointable
                )
            repoint_record = cname
            steps.append(_repoint_step(client, settings, cname, target))
    return RoutePlan(
        name,
        route,
        steps,
        not existing,
        tunnel_id,
        new_ingress,
        create_dns,
        repoint_record,
    )


def _repoint_step(
    client: CloudflareClient, settings: CloudflareSettings, record: dict, target: str
) -> str:
    old = str(record.get("content", ""))
    tunnel_id = tunnel_id_from_target(old)
    note = ""
    if tunnel_id:
        match = next(
            (t for t in _safe_tunnels(client, settings) if t["id"] == tunnel_id), None
        )
        if match is None:
            note = " (a tunnel that no longer exists in this account)"
        else:
            note = (
                f" (tunnel '{match['name']}', status {match.get('status', 'unknown')})"
            )
            if match.get("status") in ("healthy", "degraded"):
                note += " — WARNING: that tunnel is in use, its site will stop working"
    return (
        f"Repoint the existing DNS record {record['name']} from {old}{note} to {target}"
    )


def _safe_tunnels(client: CloudflareClient, settings: CloudflareSettings) -> list[dict]:
    try:
        return client.list_tunnels(settings.account_id)
    except CloudflareError:
        return []


@dataclass
class DnsEntry:
    type: str
    name: str
    content: str
    proxied: bool
    note: str = ""  # e.g. "tunnel raspi-manager-raspi-v"


def _describe_target(content: str, tunnel_names: dict[str, str]) -> str:
    tunnel_id = tunnel_id_from_target(content)
    if tunnel_id is None:
        return ""
    name = tunnel_names.get(tunnel_id)
    return (
        f"tunnel {name}"
        if name
        else "tunnel not found (deleted, or in another account)"
    )


def _tunnel_names(
    client: CloudflareClient, settings: CloudflareSettings
) -> dict[str, str]:
    try:
        return {t["id"]: t["name"] for t in client.list_tunnels(settings.account_id)}
    except CloudflareError:
        return {}  # the note is a convenience: never fail the listing because of it


def dns_overview(
    client: CloudflareClient, settings: CloudflareSettings
) -> list[DnsEntry]:
    """All DNS records of the zone, with the tunnel name when a CNAME points to a tunnel (read-only)."""
    names = _tunnel_names(client, settings)
    entries = [
        DnsEntry(
            r.get("type", "?"),
            r.get("name", "?"),
            str(r.get("content", "")),
            bool(r.get("proxied")),
            _describe_target(str(r.get("content", "")), names)
            if r.get("type") == "CNAME"
            else "",
        )
        for r in client.list_all_dns(settings.zone_id)
    ]
    return sorted(entries, key=lambda e: (e.name, e.type))


def _conflict_message(
    client: CloudflareClient,
    settings: CloudflareSettings,
    hostname: str,
    records: list[dict],
) -> str:
    names = _tunnel_names(client, settings)
    parts = []
    for r in records:
        target = f"{r.get('type', '?')} → {r.get('content', '?')}"
        note = (
            _describe_target(str(r.get("content", "")), names)
            if r.get("type") == "CNAME"
            else ""
        )
        parts.append(f"{target} ({note})" if note else target)
    return (
        f"{hostname} already has a DNS record: {'; '.join(parts)}. raspi-manager will not overwrite it. "
        "Open “DNS records…” to see it. A single CNAME can be repointed from Add route…; otherwise delete or edit it in the Cloudflare dashboard, or choose another hostname."
    )


@dataclass
class ApplyResult:
    tunnel_id: str
    created_tunnel: bool
    messages: list[str]


def apply_add_route(
    client: CloudflareClient, settings: CloudflareSettings, plan: RoutePlan
) -> ApplyResult:
    """Executes the plan. Order matters: tunnel, then ingress, then DNS (nothing points to a missing rule)."""
    messages: list[str] = []
    tunnel_id = plan.tunnel_id
    if plan.create_tunnel:
        tunnel = client.create_tunnel(settings.account_id, plan.tunnel_name)
        tunnel_id = tunnel["id"]  # its connector token is deliberately not kept here
        messages.append(f"Tunnel '{plan.tunnel_name}' created")
    if plan.create_tunnel or plan.route not in routes_of(
        client.get_ingress(settings.account_id, tunnel_id)
    ):
        client.put_ingress(settings.account_id, tunnel_id, plan.ingress)
        messages.append(f"Route {plan.route.hostname} → {plan.route.service} saved")
    if plan.create_dns:
        client.create_cname(
            settings.zone_id, plan.route.hostname, f"{tunnel_id}.cfargotunnel.com"
        )
        messages.append(f"DNS record {plan.route.hostname} created")
    elif plan.repoint_record:
        old = plan.repoint_record.get("content", "?")
        client.repoint_cname(
            settings.zone_id, plan.repoint_record, f"{tunnel_id}.cfargotunnel.com"
        )
        messages.append(
            f"DNS record {plan.route.hostname} repointed (previous target: {old})"
        )
    return ApplyResult(tunnel_id, plan.create_tunnel, messages)


def remove_route(
    client: CloudflareClient,
    settings: CloudflareSettings,
    tunnel_id: str,
    hostname: str,
) -> str:
    """Drops the ingress rule only; the DNS record is left for the developer to delete."""
    ingress = client.get_ingress(settings.account_id, tunnel_id)
    if hostname not in {r.hostname for r in routes_of(ingress)}:
        raise RouteError(f"{hostname} is not routed by this tunnel")
    client.put_ingress(settings.account_id, tunnel_id, without_route(ingress, hostname))
    return f"Route {hostname} removed (its DNS record was not touched)"


def fetch_connector_token(
    client: CloudflareClient, settings: CloudflareSettings, tunnel_id: str
) -> str:
    """Secret: used only to install the connector on the Pi, never displayed or logged."""
    return client.tunnel_token(settings.account_id, tunnel_id)


__all__ = [
    "ApplyResult",
    "CloudflareError",
    "DnsConflictError",
    "DnsEntry",
    "RoutePlan",
    "TunnelInfo",
    "apply_add_route",
    "dns_overview",
    "fetch_connector_token",
    "overview",
    "parse_tunnel_name",
    "plan_add_route",
    "remove_route",
    "tunnel_name_for",
]
