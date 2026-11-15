"""`raspi host install-tools`: ffmpeg (apt) and yt-dlp (official release) on a Pi."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

import typer

from raspi.core.models import Host
from raspi.core.tools import TOOLS, ToolError
from raspi.services.tools import install_tools, plan_tools
from raspi.ssh.transport import SshTransport

LiveHost = Callable[[str], tuple[Host, Host]]


def _fail(message: str) -> typer.Exit:
    typer.secho(message, fg="red", err=True)
    return typer.Exit(1)


def register_tools(host_app: typer.Typer, live_host: LiveHost) -> None:
    @host_app.command("install-tools")
    def host_install_tools(
        name: str,
        tools: Annotated[
            list[str] | None,
            typer.Argument(help=f"Which tools: {', '.join(TOOLS)} (default: both)"),
        ] = None,
        dry_run: bool = typer.Option(
            False, "--dry-run", help="Show it, change nothing"
        ),
        yes: bool = typer.Option(False, "--yes", help="Skip the confirmation"),
    ) -> None:
        """Install ffmpeg (apt) and/or yt-dlp (official release, checksum verified) on the Pi.

        yt-dlp is checked and run once before it replaces anything; run again to update it.
        """
        current, _ = live_host(name)
        transport = SshTransport()
        try:
            plan = plan_tools(current, transport, set(tools or TOOLS))
        except ToolError as exc:
            raise _fail(str(exc)) from exc
        for note in plan.notes:
            typer.echo(f"  {note}")
        if plan.nothing_to_do:
            typer.echo("Nothing to install.")
            return
        for step in plan.steps:
            typer.echo(f"  - {step}")
        if dry_run:
            typer.echo(f"{chr(10)}Script run on {name}:{chr(10)}{plan.script}")
            typer.echo("Dry run: nothing was changed.")
            return
        if not yes:
            typer.confirm(f"Apply these changes on {name}?", abort=True)
        result = install_tools(current, transport, plan)
        for message in result.messages:
            typer.echo(f"  {message}")
        if not result.ok:
            raise typer.Exit(1)
        typer.secho("✓ Done.", fg="green")
