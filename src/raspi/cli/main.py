from __future__ import annotations

import sys
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from raspi.cli.provision import register_provision
from raspi.cli.route import route_app
from raspi.cli.update import register_update
from raspi.config.loader import ConfigError, load_hosts
from raspi.config.writer import add_host, remove_host, update_host
from raspi.core.models import Host
from raspi.services.actions import LookupError_, find_app, read_logs, run_action
from raspi.services.adopt import add_apps, candidate_to_app, scan_host
from raspi.services.inventory import plan_publish, publish, resolve_all
from raspi.services.status import collect_all
from raspi.ssh.transport import SshTransport

app = typer.Typer(
    help="raspi-manager: manage apps on your Raspberry Pi. Without a command, opens the app window.",
    invoke_without_command=True,
)


@app.callback()
def _open_window_by_default(ctx: typer.Context) -> None:
    for stream in (sys.stdout, sys.stderr):  # cp1252 consoles cannot print ✓ / ✕
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if ctx.invoked_subcommand is None:
        from raspi.gui.app import run

        raise typer.Exit(run())


GLYPH = {
    "running": "● running",
    "stopped": "○ stopped",
    "failed": "▲ failed",
    "working": "◌ working",
    "unknown": "? unknown",
    "no-unit": "– no unit",
}
COLOR = {
    "running": "green",
    "stopped": "dim",
    "failed": "red",
    "working": "yellow",
    "unknown": "dim",
    "no-unit": "dim",
}


def _hosts() -> list[Host]:
    try:
        return load_hosts()
    except ConfigError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(1) from exc


def _live_hosts() -> list[Host]:
    """Hosts with the apps their Pi really declares (falls back to hosts.yaml)."""
    return resolve_all(_hosts(), SshTransport())


def _resolve(hosts: list[Host], app_name: str, host_name: str | None):
    """Find the app; --host is only needed when the name exists on several hosts."""
    matches = [h.name for h in hosts if any(a.name == app_name for a in h.apps)]
    if host_name is None:
        if len(matches) != 1:
            where = ", ".join(matches) or "no host"
            typer.secho(
                f"App '{app_name}' is on: {where}. Use --host.", fg="red", err=True
            )
            raise typer.Exit(1)
        host_name = matches[0]
    try:
        return find_app(hosts, host_name, app_name)
    except LookupError_ as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(1) from exc


@app.command()
def status() -> None:
    """Show the live state of every app on every Pi (read-only)."""
    console = Console()
    exit_code = 0
    for hs in collect_all(_hosts(), SshTransport()):
        if not hs.reachable:
            console.print(f"[red]✕ {hs.host.name} unreachable:[/red] {hs.error}")
            exit_code = 1
            continue
        table = Table(
            title=f"{hs.host.name} ({hs.host.user}@{hs.host.address})",
            title_justify="left",
        )
        for col in ("App", "State", "Version", "Since", "Restarts"):
            table.add_column(col)
        for a in hs.apps:
            version = a.rev + ("*" if a.dirty else "") if a.rev else "-"
            table.add_row(
                a.name,
                f"[{COLOR[a.state]}]{GLYPH[a.state]}[/]",
                version,
                a.since or "-",
                str(a.restarts),
            )
        console.print(table)
    raise typer.Exit(exit_code)


def _act(
    action: str, app_name: str, host_name: str | None, yes: bool, dry_run: bool
) -> None:
    host, ref = _resolve(_live_hosts(), app_name, host_name)
    if not dry_run and action != "start" and not yes:
        typer.confirm(f"{action} {ref.name} on {host.name}?", abort=True)
    result = run_action(host, ref, action, SshTransport(), dry_run=dry_run)
    if dry_run:
        typer.echo(f"Would run on {host.user}@{host.address}:\n{result.script}")
        return
    if not result.ok:
        typer.secho(f"✕ {action} failed: {result.message}", fg="red", err=True)
        raise typer.Exit(1)
    typer.secho(f"✓ {action} {ref.name} on {host.name}: now {result.state}", fg="green")


@app.command()
def start(
    app_name: str,
    host: str | None = typer.Option(None, help="Host name (needed if ambiguous)"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the command, run nothing"
    ),
) -> None:
    """Start an app."""
    _act("start", app_name, host, True, dry_run)


@app.command()
def stop(
    app_name: str,
    host: str | None = typer.Option(None, help="Host name (needed if ambiguous)"),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the command, run nothing"
    ),
) -> None:
    """Stop an app (asks for confirmation)."""
    _act("stop", app_name, host, yes, dry_run)


@app.command()
def restart(
    app_name: str,
    host: str | None = typer.Option(None, help="Host name (needed if ambiguous)"),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the command, run nothing"
    ),
) -> None:
    """Restart an app (asks for confirmation)."""
    _act("restart", app_name, host, yes, dry_run)


@app.command()
def logs(
    app_name: str,
    host: str | None = typer.Option(None, help="Host name (needed if ambiguous)"),
    lines: int = typer.Option(50, "-n", "--lines", help="Number of lines (max 1000)"),
) -> None:
    """Show the last journal lines of an app (read-only)."""
    h, ref = _resolve(_live_hosts(), app_name, host)
    result = read_logs(h, ref, SshTransport(), lines)
    if not result.ok:
        typer.secho(f"✕ {result.message}", fg="red", err=True)
        raise typer.Exit(1)
    typer.echo(result.message)


host_app = typer.Typer(help="Manage the Pi registered in ~/.raspi/hosts.yaml.")
app.add_typer(host_app, name="host")
app.add_typer(route_app, name="route")


@host_app.command("add")
def host_add(name: str, address: str, user: str) -> None:
    """Register a Pi (creates hosts.yaml if needed). Nothing is sent to the Pi."""
    try:
        add_host(name, address, user)
    except ConfigError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(1) from exc
    typer.secho(f"✓ Added {name} ({user}@{address})", fg="green")


@host_app.command("ls")
def host_ls() -> None:
    """List the registered Pi and how many apps each has (reads hosts.yaml only)."""
    for h in _hosts():
        typer.echo(f"{h.name}\t{h.user}@{h.address}\t{len(h.apps)} app(s)")


@host_app.command("edit")
def host_edit(
    name: str,
    address: str | None = typer.Option(None, help="New address"),
    user: str | None = typer.Option(None, help="New login user"),
) -> None:
    """Change the address and/or user of a Pi (the name is fixed)."""
    current = next((h for h in _hosts() if h.name == name), None)
    if current is None:
        typer.secho(f"Unknown host '{name}'", fg="red", err=True)
        raise typer.Exit(1)
    try:
        update_host(name, address or current.address, user or current.user)
    except ConfigError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(1) from exc
    typer.secho(f"✓ Updated {name}", fg="green")


@host_app.command("rm")
def host_rm(
    name: str,
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation"),
) -> None:
    """Forget a Pi and its declared apps in hosts.yaml (the Pi itself is not touched)."""
    current = next((h for h in _hosts() if h.name == name), None)
    if current is None:
        typer.secho(f"Unknown host '{name}'", fg="red", err=True)
        raise typer.Exit(1)
    if not yes:
        apps = ", ".join(a.name for a in current.apps) or "no app"
        typer.confirm(f"Remove {name} ({apps}) from hosts.yaml?", abort=True)
    try:
        remove_host(name)
    except ConfigError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(1) from exc
    typer.secho(f"✓ Removed {name}", fg="green")


@host_app.command("publish")
def host_publish(
    name: str,
    dry_run: bool = typer.Option(False, "--dry-run", help="Show it, write nothing"),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation"),
) -> None:
    """Write the apps declared in hosts.yaml to ~/.raspi/apps.yaml on that Pi (first time only)."""
    current = next((h for h in _hosts() if h.name == name), None)
    if current is None:
        typer.secho(f"Unknown host '{name}'", fg="red", err=True)
        raise typer.Exit(1)
    transport = SshTransport()
    plan = plan_publish(current, transport)
    if not plan.ok:
        typer.secho(plan.message, fg="red", err=True)
        raise typer.Exit(1)
    typer.echo(f"Would write {plan.path} on {name}:\n{plan.content}")
    if not dry_run and not yes:
        typer.confirm(f"Write it on {name}?", abort=True)
    result = publish(current, plan, transport, dry_run=dry_run)
    if not result.ok:
        typer.secho(f"✕ {result.message}", fg="red", err=True)
        raise typer.Exit(1)
    typer.secho(f"✓ {result.message}", fg="green")


def _live_host(name: str) -> tuple[Host, Host]:
    """(host as configured, host with the apps its Pi really declares)."""
    current = next((h for h in _hosts() if h.name == name), None)
    if current is None:
        typer.secho(f"Unknown host '{name}'", fg="red", err=True)
        raise typer.Exit(1)
    return current, resolve_all([current], SshTransport())[0]


@host_app.command("scan")
def host_scan(name: str) -> None:
    """List services run by the login user that raspi-manager does not manage yet (read-only)."""
    _, live = _live_host(name)
    scan = scan_host(live, SshTransport())
    if not scan.ok:
        typer.secho(scan.message, fg="red", err=True)
        raise typer.Exit(1)
    for c in scan.candidates:
        typer.echo(f"{c.name}\t{c.state}\t{c.repo or c.workdir or '-'}\t{c.command}")
    if not scan.candidates:
        typer.echo("Nothing to add.")


@host_app.command("adopt")
def host_adopt(
    name: str,
    units: Annotated[
        list[str], typer.Argument(help="App names or unit names from `scan`")
    ],
    dry_run: bool = typer.Option(False, "--dry-run", help="Show it, write nothing"),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation"),
) -> None:
    """Add scanned services to the app inventory of that Pi (~/.raspi/apps.yaml)."""
    current, live = _live_host(name)
    transport = SshTransport()
    scan = scan_host(live, transport)
    if not scan.ok:
        typer.secho(scan.message, fg="red", err=True)
        raise typer.Exit(1)
    by_key = {key: c for c in scan.candidates for key in (c.name, c.unit)}
    unknown = [u for u in units if u not in by_key]
    if unknown:
        typer.secho(f"Not found by the scan: {', '.join(unknown)}", fg="red", err=True)
        raise typer.Exit(1)
    apps = tuple(dict.fromkeys(candidate_to_app(by_key[u]) for u in units))
    for a in apps:
        typer.echo(f"{a.name}\t{a.unit}\t{a.path or '-'}")
    if not dry_run and not yes:
        typer.confirm(
            f"Add {len(apps)} app(s) to ~/.raspi/apps.yaml on {name}?", abort=True
        )
    result = add_apps(current, apps, transport, dry_run=dry_run)
    if not result.ok:
        typer.secho(f"✕ {result.message}", fg="red", err=True)
        raise typer.Exit(1)
    typer.secho(f"✓ {result.message}", fg="green")


register_provision(host_app, lambda name: _live_host(name))
register_update(host_app, lambda name: _live_host(name))


@app.command()
def gui() -> None:
    """Open the native desktop app (live status, actions, logs)."""
    from raspi.gui.app import run

    raise typer.Exit(run())


@app.command()
def web(port: int = 8765) -> None:
    """Start the local dashboard on 127.0.0.1 (never exposed)."""
    import uvicorn

    uvicorn.run("raspi.web.app:create_app", factory=True, host="127.0.0.1", port=port)
