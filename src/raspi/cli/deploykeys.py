"""`raspi host deploy-keys|deploy-key-rotate|deploy-key-rm`: look after the deploy keys already on a Pi."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

import typer

from raspi.core.deploykey_manage import delete_steps, rotate_steps
from raspi.core.models import Host
from raspi.services.deploykey import (
    delete_key,
    fetch_github_keys,
    list_keys,
    rotate_key,
    verify_repo,
)
from raspi.ssh.transport import SshTransport

LiveHost = Callable[[str], tuple[Host, Host]]


def _fail(message: str) -> typer.Exit:
    typer.secho(message, fg="red", err=True)
    return typer.Exit(1)


def register_deploykeys(host_app: typer.Typer, live_host: LiveHost) -> None:
    @host_app.command("deploy-keys")
    def host_deploy_keys(
        name: str,
        check: bool = typer.Option(
            False,
            "--check",
            help="Also ask GitHub, per key, whether the repository accepts it",
        ),
    ) -> None:
        """List the deploy keys on the Pi (read-only): app, fingerprint, repository, use."""
        current, _ = live_host(name)
        transport = SshTransport()
        listing = list_keys(current, transport)
        if not listing.ok:
            raise _fail(f"✕ {listing.message}")
        apps = {a.name: a for a in current.apps}
        if not listing.keys:
            typer.echo(f"No deploy key made by raspi-manager on {name}.")
            return
        refused = False
        for key in listing.keys:
            app = apps.get(key.name)
            use = (
                f"used by {app.repo or 'an app without repo'}"
                if app
                else "no app uses it"
            )
            state = (
                ""
                if key.complete
                else "  ⚠ incomplete: "
                + ("no ssh alias" if key.has_files else "no key file")
            )
            typer.echo(f"{key.name}  {key.fingerprint or '-'}  {use}{state}")
            if check and key.has_files and app and app.repo:
                checked = verify_repo(current, transport, key.name, app.repo)
                refused |= not checked.ok
                typer.echo(f"    {'✓' if checked.ok else '✕'} {checked.message}")
        if refused:
            raise typer.Exit(1)

    @host_app.command("deploy-key-rotate")
    def host_deploy_key_rotate(
        name: str,
        app_name: Annotated[str, typer.Argument(metavar="APP")],
        dry_run: bool = typer.Option(
            False, "--dry-run", help="Show it, change nothing"
        ),
        yes: bool = typer.Option(False, "--yes", help="Skip the confirmation"),
    ) -> None:
        """Replace the deploy key of APP by a new one and print its public key."""
        current, _ = live_host(name)
        for step in rotate_steps(app_name, name):
            typer.echo(f"  - {step}")
        if not dry_run and not yes:
            typer.confirm(f"Rotate the key of {app_name} on {name}?", abort=True)
        made = rotate_key(
            current, SshTransport(), app_name, dry_run=dry_run, lookup=fetch_github_keys
        )
        if dry_run and made.script:
            typer.echo(f"\nScript run on {name}:\n{made.script}")
        if not made.ok:
            raise _fail(f"✕ {made.message}")
        typer.secho(f"✓ {made.message}", fg="green")
        if not dry_run:
            typer.echo(
                f"\nAdd this public key on GitHub (read-only):\n{made.public_key}"
            )

    @host_app.command("deploy-key-rm")
    def host_deploy_key_rm(
        name: str,
        app_name: Annotated[str, typer.Argument(metavar="APP")],
        dry_run: bool = typer.Option(
            False, "--dry-run", help="Show it, change nothing"
        ),
        yes: bool = typer.Option(False, "--yes", help="Skip the confirmation"),
    ) -> None:
        """Delete the deploy key of APP and its ssh alias from the Pi."""
        current, _ = live_host(name)
        for step in delete_steps(app_name, name):
            typer.echo(f"  - {step}")
        if not dry_run and not yes:
            typer.confirm(f"Delete the key of {app_name} on {name}?", abort=True)
        done = delete_key(current, SshTransport(), app_name, dry_run=dry_run)
        if dry_run and done.script:
            typer.echo(f"\nScript run on {name}:\n{done.script}")
        if not done.ok:
            raise _fail(f"✕ {done.message}")
        typer.secho(f"✓ {done.message}", fg="green")
