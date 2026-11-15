"""`raspi route add`: put an app on the internet (Cloudflare route + connector on its Pi)."""

from __future__ import annotations

import typer

from raspi.cloudflare.client import CloudflareClient, CloudflareError
from raspi.config import cloudflare_settings as store
from raspi.config.loader import ConfigError, load_hosts
from raspi.core.connector import ConnectorError
from raspi.core.routes import RouteError
from raspi.services import app_route, routing
from raspi.services.inventory import resolve_all
from raspi.ssh.transport import SshTransport

route_app = typer.Typer(help="Put an app on the internet through Cloudflare.")


def _fail(message: str) -> typer.Exit:
    typer.secho(message, fg="red", err=True)
    return typer.Exit(1)


def _client_and_settings() -> tuple[CloudflareClient, store.CloudflareSettings]:
    try:
        token = store.get_token()
    except store.SecretStoreError as exc:
        raise _fail(str(exc)) from exc
    settings = store.load_settings()
    if not token or not settings.complete:
        raise _fail(
            "Cloudflare is not set up: open the app's “Cloudflare…” window, save the "
            "API token and the zone, then retry."
        )
    return CloudflareClient(token), settings


def _find(app_name: str, host_name: str | None):
    try:
        hosts = resolve_all(load_hosts(), SshTransport())
    except ConfigError as exc:
        raise _fail(str(exc)) from exc
    matches = [
        (h, a)
        for h in hosts
        if host_name in (None, h.name)
        for a in h.apps
        if a.name == app_name
    ]
    if len(matches) != 1:
        where = ", ".join(h.name for h, _ in matches) or "no host"
        raise _fail(f"App '{app_name}' is on: {where}. Use --host.")
    return matches[0]


@route_app.command("add")
def route_add(
    hostname: str,
    target: str = typer.Argument(..., metavar="APP[:PORT]"),
    host: str | None = typer.Option(None, help="Host name (needed if ambiguous)"),
    repoint: bool = typer.Option(
        False, "--repoint", help="Point an existing CNAME at the new tunnel"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show everything, change nothing"
    ),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation"),
) -> None:
    """Create the tunnel, route and DNS record for HOSTNAME, and run the connector on the Pi."""
    app_name, _, port_text = target.partition(":")
    pi, app = _find(app_name, host)
    transport = SshTransport()
    if not port_text:
        ports = app_route.detect_ports(pi, app, transport)
        if len(ports) != 1:
            hint = f"found {', '.join(map(str, ports))}" if ports else "none found"
            raise _fail(f"Cannot pick the port ({hint}). Use {app_name}:PORT.")
        port_text = str(ports[0])
    client, settings = _client_and_settings()
    try:
        plan = app_route.plan_app_route(
            client, settings, pi, app, hostname, port_text, transport, repoint
        )
    except routing.DnsConflictError as exc:
        extra = (
            " Use --repoint to point it at the new tunnel." if exc.repointable else ""
        )
        raise _fail(f"{exc}{extra}") from exc
    except (RouteError, CloudflareError, ConnectorError) as exc:
        raise _fail(str(exc)) from exc
    if plan.nothing_to_do:
        typer.echo("Already set up: nothing to change.")
        return
    typer.echo(f"{app.name} on {pi.name}, port {plan.port}:")
    for step in plan.steps:
        typer.echo(f"  - {step}")
    if dry_run:
        if plan.install:
            typer.echo(
                f"\nScript run on {pi.name} (token hidden):\n{plan.preview_script()}"
            )
        typer.echo("Dry run: nothing was changed.")
        return
    if not yes:
        typer.confirm("Apply these changes?", abort=True)
    result = app_route.apply_app_route(client, settings, plan, transport)
    for message in result.messages:
        typer.echo(f"  {message}")
    if not result.ok:
        raise typer.Exit(1)
    typer.secho("✓ Route set up.", fg="green")
