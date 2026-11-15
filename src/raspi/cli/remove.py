"""`raspi host remove-app`: remove an app from a Pi, only what is asked for."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

import typer

from raspi.core.models import Host
from raspi.core.remove import ENV, INVENTORY, KEY, LOCAL, UNIT, WORKDIR
from raspi.services.inventory import read_inventory
from raspi.services.remove import RemoveError, apply_remove, plan_remove
from raspi.ssh.transport import SshTransport

LiveHost = Callable[[str], tuple[Host, Host]]


def _fail(message: str) -> typer.Exit:
    typer.secho(message, fg="red", err=True)
    return typer.Exit(1)


def register_remove(host_app: typer.Typer, live_host: LiveHost) -> None:
    @host_app.command("remove-app")
    def host_remove_app(
        name: str,
        app_name: Annotated[str, typer.Argument(metavar="APP")],
        workdir: bool = typer.Option(
            False, "--workdir", help="Also delete the working copy"
        ),
        key: bool = typer.Option(
            False, "--key", help="Also delete the deploy key and alias"
        ),
        env: bool = typer.Option(
            False, "--env", help="Also delete the env file(s) on the Pi"
        ),
        local_env: bool = typer.Option(
            False, "--local-env", help="Also delete the local env copies on this PC"
        ),
        keep_inventory: bool = typer.Option(
            False, "--keep-inventory", help="Leave the entry in the Pi's app list"
        ),
        keep_unit: bool = typer.Option(
            False, "--keep-unit", help="Leave the systemd unit"
        ),
        dry_run: bool = typer.Option(
            False, "--dry-run", help="Show it, change nothing"
        ),
        yes: bool = typer.Option(False, "--yes", help="Skip typing the app name"),
    ) -> None:
        """Remove APP: its list entry and unit by default, the rest only when asked.

        Never touched: the app's prerequisite folders (its data), anything outside the home
        folder, and the Cloudflare route and tunnel.
        """
        current, live = live_host(name)
        app = next((a for a in live.apps if a.name == app_name), None)
        if app is None:
            raise _fail(f"{app_name} is not in the inventory of {name}")
        transport = SshTransport()
        on_pi = read_inventory(current, transport).source == "pi"
        try:
            plan = plan_remove(live, transport, app, on_pi)
        except RemoveError as exc:
            raise _fail(str(exc)) from exc
        wanted = {
            INVENTORY: not keep_inventory,
            UNIT: not keep_unit,
            WORKDIR: workdir,
            KEY: key,
            ENV: env,
            LOCAL: local_env,
        }
        items = {i.key: i for i in plan.items}
        chosen = set()
        for item_key, asked in wanted.items():
            if not asked:
                continue
            item = items[item_key]
            if item.available:
                chosen.add(item_key)
            elif item_key in (
                WORKDIR,
                KEY,
                ENV,
                LOCAL,
            ):  # asked explicitly: say why not
                raise _fail(f"Cannot remove '{item.label}': {item.note}")
            else:
                typer.secho(f"  skipped: {item.label} ({item.note})", fg="yellow")
        if not chosen:
            typer.echo("Nothing to remove.")
            return
        for item_key in (INVENTORY, UNIT, WORKDIR, KEY, ENV, LOCAL):
            if item_key in chosen:
                typer.echo(f"  - {items[item_key].label}")
        if chosen - {INVENTORY, LOCAL}:
            typer.echo(
                f"{chr(10)}Script run on {name}:{chr(10)}{plan.script_for(chosen)}"
            )
        if dry_run:
            typer.echo("Dry run: nothing was changed.")
            return
        if not yes and typer.prompt("Type the app name to confirm") != app_name:
            raise _fail("The name does not match: nothing was removed.")
        result = apply_remove(live, transport, plan, chosen)
        for message in result.messages:
            typer.echo(f"  {message}")
        if not result.ok:
            raise typer.Exit(1)
        typer.secho("✓ Done.", fg="green")
