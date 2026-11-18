"""`raspi host update|rollback`: move an installed app to another revision."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

import typer

from raspi.core.models import Host
from raspi.core.update import UpdateError
from raspi.services.update import UpdatePlan, apply_update, plan_update
from raspi.ssh.transport import SshTransport

LiveHost = Callable[[str], tuple[Host, Host]]


def _fail(message: str) -> typer.Exit:
    typer.secho(message, fg="red", err=True)
    return typer.Exit(1)


def _show(plan: UpdatePlan) -> None:
    for step in plan.steps:
        typer.echo(f"  - {step}")
    for note in plan.warnings:
        typer.secho(f"  ! {note}", fg="yellow")
    for commit in plan.commits:
        typer.echo(f"    {commit}")


def register_update(host_app: typer.Typer, live_host: LiveHost) -> None:
    def run(
        name: str,
        app_name: str,
        ref: str,
        rollback: bool,
        dry_run: bool,
        yes: bool,
        no_restart: bool,
        keep: list[str] | None = None,
    ) -> None:
        _, live = live_host(name)
        app = next((a for a in live.apps if a.name == app_name), None)
        if app is None:
            raise _fail(f"{app_name} is not in the inventory of {name}")
        transport = SshTransport()
        try:
            plan = plan_update(live, transport, app, ref, rollback)
        except UpdateError as exc:
            raise _fail(str(exc)) from exc
        if no_restart:
            plan.restart = False
        for name_kept in keep or []:
            kept = next((p for p in plan.processes if p.name == name_kept), None)
            if kept is None:
                raise _fail(f"{app_name} has no process called {name_kept}")
            kept.restart = False
        if plan.nothing_to_do:
            typer.echo(
                f"{app_name} is already at {plan.new[:7]} and built: nothing to do."
            )
            return
        _show(plan)
        if dry_run:
            typer.echo(f"{chr(10)}Script run on {name}:{chr(10)}{plan.script}")
            typer.echo("Dry run: nothing was changed.")
            return
        if not yes:
            typer.confirm(f"Apply these changes on {name}?", abort=True)
        result = apply_update(live, transport, plan)
        for message in result.messages:
            typer.echo(f"  {message}")
        if not result.ok:
            raise typer.Exit(1)
        typer.secho("✓ Done.", fg="green")

    @host_app.command("update")
    def host_update(
        name: str,
        app_name: Annotated[str, typer.Argument(metavar="APP")],
        ref: str = typer.Option(
            "", "--ref", help="Branch, tag or commit (default: latest)"
        ),
        dry_run: bool = typer.Option(
            False, "--dry-run", help="Show it, change nothing"
        ),
        yes: bool = typer.Option(False, "--yes", help="Skip the confirmation"),
        no_restart: bool = typer.Option(
            False, "--no-restart", help="Do not restart the app"
        ),
        keep: Annotated[
            list[str] | None,
            typer.Option(
                "--keep", help="Do not restart this process this time (repeatable)"
            ),
        ] = None,
    ) -> None:
        """Check out another revision of APP, rebuild it and restart what was running.

        A process whose rule is `keep` is never restarted; `ask` ones are restarted
        unless named with --keep."""
        run(name, app_name, ref, False, dry_run, yes, no_restart, keep)

    @host_app.command("rollback")
    def host_rollback(
        name: str,
        app_name: Annotated[str, typer.Argument(metavar="APP")],
        dry_run: bool = typer.Option(
            False, "--dry-run", help="Show it, change nothing"
        ),
        yes: bool = typer.Option(False, "--yes", help="Skip the confirmation"),
        no_restart: bool = typer.Option(
            False, "--no-restart", help="Do not restart the app"
        ),
        keep: Annotated[
            list[str] | None,
            typer.Option(
                "--keep", help="Do not restart this process this time (repeatable)"
            ),
        ] = None,
    ) -> None:
        """Return APP to the revision built before its last update (or to the last build)."""
        run(name, app_name, "", True, dry_run, yes, no_restart, keep)
