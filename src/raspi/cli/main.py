from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from raspi.config.loader import ConfigError, load_hosts
from raspi.core.models import Host
from raspi.services.actions import LookupError_, find_app, read_logs, run_action
from raspi.services.status import collect_all
from raspi.ssh.transport import SshTransport

app = typer.Typer(
    help="raspi-manager: manage apps on your Raspberry Pi. Without a command, opens the app window.",
    invoke_without_command=True,
)


@app.callback()
def _open_window_by_default(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        from raspi.gui.app import run

        raise typer.Exit(run())


GLYPH = {
    "running": "● running",
    "stopped": "○ stopped",
    "failed": "▲ failed",
    "working": "◌ working",
    "unknown": "? unknown",
}
COLOR = {
    "running": "green",
    "stopped": "dim",
    "failed": "red",
    "working": "yellow",
    "unknown": "dim",
}


def _hosts() -> list[Host]:
    try:
        return load_hosts()
    except ConfigError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(1) from exc


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
    host, ref = _resolve(_hosts(), app_name, host_name)
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
    h, ref = _resolve(_hosts(), app_name, host)
    result = read_logs(h, ref, SshTransport(), lines)
    if not result.ok:
        typer.secho(f"✕ {result.message}", fg="red", err=True)
        raise typer.Exit(1)
    typer.echo(result.message)


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
