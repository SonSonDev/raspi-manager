"""Put an app on the internet: Cloudflare tunnel + route + DNS, and the connector on its Pi.

`plan_app_route` only reads (Cloudflare API, a few read-only commands on the Pi).
`apply_app_route` does the changes, in this order: Cloudflare (tunnel, ingress, DNS),
connector on the Pi (token fetched just now, never shown or logged), then checks.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from raspi.cloudflare.client import CloudflareClient, CloudflareError
from raspi.cloudflare.release import fetch_download
from raspi.config.cloudflare_settings import CloudflareSettings
from raspi.core.connector import (
    DEFAULT_BIN,
    HIDDEN,
    ConnectorError,
    ConnectorState,
    Download,
    build_check_script,
    build_install_script,
    build_ports_script,
    install_steps,
    parse_check,
    parse_ports,
    render_unit,
    unit_name_for,
)
from raspi.core.models import AppRef, Host
from raspi.core.routes import RouteError
from raspi.services import routing
from raspi.ssh.transport import Transport

DownloadLookup = Callable[[str], Download]
HttpGet = Callable[[str], int]


def detect_ports(host: Host, app: AppRef, transport: Transport) -> list[int]:
    """Read-only: TCP ports the app's processes listen on (empty: not running or unknown)."""
    result = transport.run_script(host, build_ports_script(app.unit))
    return parse_ports(result.stdout) if result.ok else []


def check_port(value: int | str) -> int:
    try:
        port = int(str(value).strip())
    except ValueError:
        port = 0
    if not 0 < port < 65536:
        raise RouteError("The port must be a number between 1 and 65535")
    return port


@dataclass
class AppRoutePlan:
    host: Host
    app: AppRef
    port: int
    cloudflare: routing.RoutePlan
    unit: str
    state: ConnectorState
    download: Download | None  # None: cloudflared is already on the Pi
    install: bool  # the connector unit must be created
    pi_steps: list[str] = field(default_factory=list)
    replace_reason: str = ""  # set when a running connector is replaced, and why

    @property
    def nothing_to_do(self) -> bool:
        return self.cloudflare.nothing_to_do and not self.install

    @property
    def steps(self) -> list[str]:
        return self.cloudflare.steps + [
            f"On {self.host.name}: {s}" for s in self.pi_steps
        ]

    def preview_script(self) -> str:
        """The exact script the Pi would run, with the token hidden."""
        return build_install_script(self.unit, self._unit_text(), HIDDEN, self.download)

    def _unit_text(self) -> str:
        return render_unit(
            self.cloudflare.tunnel_name, self.unit, self.state.bin_path or DEFAULT_BIN
        )


def _stale_reason(
    client: CloudflareClient, settings: CloudflareSettings, plan: routing.RoutePlan
) -> str:
    """Why a connector that systemd reports as running cannot be trusted ('' when it can).

    cloudflared stays "active" while it retries forever with a token whose tunnel was deleted
    ("Tunnel not found"), so the unit state alone proves nothing: ask Cloudflare.
    """
    if plan.create_tunnel:
        return (
            "the tunnel is new, so the connector already running here belongs to "
            "another (deleted) tunnel"
        )
    try:
        found = client.list_tunnels(settings.account_id, plan.tunnel_name)
    except CloudflareError:
        return ""  # cannot tell: keep what runs
    status = found[0].get("status", "unknown") if found else "unknown"
    if status in ("healthy", "degraded"):
        return ""
    return (
        f"the tunnel is {status}: the connector running here is not connected "
        "(its token is probably outdated)"
    )


def plan_app_route(
    client: CloudflareClient,
    settings: CloudflareSettings,
    host: Host,
    app: AppRef,
    hostname: str,
    port: int | str,
    transport: Transport,
    repoint: bool = False,
    lookup: DownloadLookup = fetch_download,
) -> AppRoutePlan:
    """Read-only. Raises RouteError / CloudflareError / ConnectorError / DnsConflictError."""
    port = check_port(port)
    cloudflare = routing.plan_add_route(
        client,
        settings,
        host.name,
        hostname,
        f"http://localhost:{port}",
        repoint,
        project=app.name,
    )
    unit = unit_name_for(cloudflare.tunnel_name)
    probe = transport.run_script(host, build_check_script(unit))
    if not probe.ok:
        raise ConnectorError(f"Cannot inspect {host.name}: {probe.error}")
    state = parse_check(probe.stdout)
    running = state.unit_loaded and state.unit_active == "active"
    stale = _stale_reason(client, settings, cloudflare) if running else ""
    if running and not stale:
        return AppRoutePlan(host, app, port, cloudflare, unit, state, None, False)
    if not state.has_sudo:
        raise ConnectorError(
            f"{host.name} needs passwordless sudo (sudo -n) to install the connector"
        )
    download = None
    if not state.bin_path:
        if not state.has_curl:
            raise ConnectorError(
                f"cloudflared and curl are both missing on {host.name}"
            )
        download = lookup(state.machine)
    bin_path = state.bin_path or DEFAULT_BIN
    return AppRoutePlan(
        host,
        app,
        port,
        cloudflare,
        unit,
        state,
        download,
        True,
        install_steps(unit, download, bin_path, replace=running),
        stale,
    )


@dataclass
class AppRouteResult:
    ok: bool
    messages: list[str]
    tunnel_id: str = ""


def _http_status(url: str) -> int:
    return httpx.get(url, timeout=10, follow_redirects=False).status_code


def verify_route(
    client: CloudflareClient,
    settings: CloudflareSettings,
    plan: AppRoutePlan,
    sleep: Callable[[float], None] = time.sleep,
    http_get: HttpGet = _http_status,
    tries: int = 15,
) -> list[str]:
    """Wait for the tunnel to report healthy, then ask the public URL once. Never raises."""
    name = plan.cloudflare.tunnel_name
    status = "unknown"
    for attempt in range(tries):
        try:
            found = client.list_tunnels(settings.account_id, name)
        except CloudflareError:
            found = []
        status = found[0].get("status", "unknown") if found else "unknown"
        if status in ("healthy", "degraded"):
            break
        if attempt + 1 < tries:
            sleep(3)
    messages = [
        f"Tunnel '{name}' is {status}"
        + ("" if status in ("healthy", "degraded") else ": check again in a minute")
    ]
    url = f"https://{plan.cloudflare.route.hostname}"
    try:
        code = http_get(url)
        hint = (
            " (the app does not answer on that port)" if code in (502, 503, 504) else ""
        )
        messages.append(f"{url} answered {code}{hint}")
    except httpx.HTTPError as exc:
        messages.append(
            f"{url} did not answer yet ({type(exc).__name__}); DNS may need a moment"
        )
    return messages


def apply_app_route(
    client: CloudflareClient,
    settings: CloudflareSettings,
    plan: AppRoutePlan,
    transport: Transport,
    sleep: Callable[[float], None] = time.sleep,
    http_get: HttpGet = _http_status,
) -> AppRouteResult:
    messages: list[str] = []
    try:
        applied = routing.apply_add_route(client, settings, plan.cloudflare)
    except (CloudflareError, RouteError) as exc:
        return AppRouteResult(False, [f"Cloudflare step failed: {exc}"])
    messages += applied.messages
    if plan.install:
        try:
            token = routing.fetch_connector_token(client, settings, applied.tunnel_id)
        except CloudflareError as exc:
            return AppRouteResult(
                False,
                messages + [f"Could not get the connector token: {exc}"],
                applied.tunnel_id,
            )
        script = build_install_script(
            plan.unit, plan._unit_text(), token, plan.download
        )
        result = transport.run_script(plan.host, script, timeout=300)
        if not result.ok:
            reason = result.error.replace(
                token, HIDDEN
            )  # belt and braces: never show it
            return AppRouteResult(
                False,
                messages
                + [f"Installing the connector on {plan.host.name} failed: {reason}"],
                applied.tunnel_id,
            )
        messages.append(
            f"Connector {plan.unit} installed and started on {plan.host.name}"
        )
    messages += verify_route(client, settings, plan, sleep, http_get)
    return AppRouteResult(True, messages, applied.tunnel_id)
