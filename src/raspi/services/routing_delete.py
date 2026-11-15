"""Deleting a tunnel created by raspi-manager: guarded plan, then a delete that re-checks first."""

from __future__ import annotations

from dataclasses import dataclass

from raspi.cloudflare.client import CloudflareClient
from raspi.config.cloudflare_settings import CloudflareSettings
from raspi.core.routes import Route, RouteError, routes_of
from raspi.services.routing import parse_tunnel_name

ACTIVE_STATUSES = ("healthy", "degraded")


@dataclass
class DeletePlan:
    tunnel_id: str
    name: str
    status: str
    routes: list[Route]
    dns_hostnames: list[str]  # CNAMEs that will keep pointing at the deleted tunnel


def plan_delete_tunnel(
    client: CloudflareClient,
    settings: CloudflareSettings,
    tunnel_id: str,
    host_names: list[str],
) -> DeletePlan:
    """Read-only. Refuses tunnels not created by raspi-manager and tunnels with active connections."""
    tunnel = next(
        (t for t in client.list_tunnels(settings.account_id) if t["id"] == tunnel_id),
        None,
    )
    if tunnel is None:
        raise RouteError("This tunnel no longer exists (refresh the list)")
    name = tunnel["name"]
    if parse_tunnel_name(name, host_names) is None:
        raise RouteError(
            f"'{name}' was not created by raspi-manager for one of your Pis: delete it in the Cloudflare dashboard if you are sure."
        )
    status = tunnel.get("status", "unknown")
    if status in ACTIVE_STATUSES:
        raise RouteError(
            f"'{name}' is {status}: a connector is still running on a Pi. Stop it there first, then delete the tunnel."
        )
    routes = (
        routes_of(client.get_ingress(settings.account_id, tunnel_id))
        if tunnel.get("config_src") == "cloudflare"
        else []
    )
    target = f"{tunnel_id}.cfargotunnel.com"
    dns = sorted(
        r["name"]
        for r in client.list_all_dns(settings.zone_id)
        if r.get("type") == "CNAME" and str(r.get("content", "")).lower() == target
    )
    return DeletePlan(tunnel_id, name, status, routes, dns)


def apply_delete_tunnel(
    client: CloudflareClient, settings: CloudflareSettings, plan: DeletePlan
) -> str:
    """Re-checks the status right before deleting (a connector may have started since the preview)."""
    current = next(
        (
            t
            for t in client.list_tunnels(settings.account_id)
            if t["id"] == plan.tunnel_id
        ),
        None,
    )
    if current is None:
        raise RouteError("This tunnel no longer exists")
    if current.get("status") in ACTIVE_STATUSES:
        raise RouteError(
            f"'{plan.name}' became {current.get('status')}: nothing was deleted"
        )
    client.delete_tunnel(settings.account_id, plan.tunnel_id)
    message = f"Tunnel '{plan.name}' deleted"
    if plan.dns_hostnames:
        message += f". These DNS records still point to it and should be removed or repointed: {', '.join(plan.dns_hostnames)}"
    return message
