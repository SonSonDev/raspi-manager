"""`raspi host move-app`: move an app and its data to another Pi."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

import typer

from raspi.core.models import Host
from raspi.core.move import MoveError
from raspi.services.move import apply_move, plan_move
from raspi.ssh.transport import SshTransport

LiveHost = Callable[[str], tuple[Host, Host]]


def _fail(message: str) -> typer.Exit:
    typer.secho(message, fg="red", err=True)
    return typer.Exit(1)


def register_move(host_app: typer.Typer, live_host: LiveHost) -> None:
    @host_app.command("move-app")
    def host_move_app(
        name: str,
        app_name: Annotated[str, typer.Argument(metavar="APP")],
        target: Annotated[str, typer.Argument(metavar="TARGET")],
        dry_run: bool = typer.Option(
            False, "--dry-run", help="Probe both Pis (read-only) and show the plan"
        ),
        yes: bool = typer.Option(False, "--yes", help="Skip the confirmation"),
    ) -> None:
        """Move APP from the Pi NAME to TARGET with its data folders and env files.

        The app must already be set up on TARGET (same revision, unit installed, not running).
        The source is left stopped and disabled; its data is never deleted.
        """
        _, source = live_host(name)
        _, dest = live_host(target)
        transport = SshTransport()
        try:
            plan = plan_move(source, dest, app_name, transport)
        except MoveError as exc:
            raise _fail(f"✕ Refused:\n{exc}") from exc
        for step in plan.steps:
            typer.echo(f"  - {step}")
        for warning in plan.warnings:
            typer.secho(f"  ! {warning}", fg="yellow")
        if dry_run:
            typer.echo(f"\nScripts:\n{plan.scripts}")
            return
        if not yes:
            typer.confirm(f"Move {app_name} from {name} to {target}?", abort=True)
        result = apply_move(plan, transport, progress=typer.echo)
        if not result.ok:
            raise _fail(f"✕ {result.message}")
        typer.secho(f"✓ {result.message}", fg="green")
