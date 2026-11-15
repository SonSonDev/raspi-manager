"""`raspi host doctor|provision|install-node|deploy-key`: get a Pi ready to run an app."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

import typer

from raspi.config.loader import ConfigError, parse_manifest, parse_requirement_line
from raspi.core.deploykey import clone_url, key_steps
from raspi.core.install import InstallError
from raspi.core.models import Host
from raspi.core.runtime import RuntimeError_
from raspi.core.unit import UnitError
from raspi.services.deploykey import ensure_key, fetch_github_keys, verify_repo
from raspi.services.install import install_app, plan_install
from raspi.services.provision import doctor, provision
from raspi.services.runtime import install_node, plan_node
from raspi.services.unit import install_unit, plan_unit
from raspi.ssh.transport import SshTransport

LiveHost = Callable[[str], tuple[Host, Host]]


def _fail(message: str) -> typer.Exit:
    typer.secho(message, fg="red", err=True)
    return typer.Exit(1)


def register_provision(host_app: typer.Typer, live_host: LiveHost) -> None:
    @host_app.command("doctor")
    def host_doctor(name: str) -> None:
        """Check what the Pi needs to run apps (read-only)."""
        current, _ = live_host(name)
        report = doctor(current, SshTransport())
        if not report.ok:
            raise _fail(report.message)
        for c in report.checks:
            glyph = "✓" if c.ok else ("✕" if c.required else "–")
            typer.echo(
                f"{glyph} {c.label}: {c.detail}" + ("" if c.ok else f"  → {c.fix}")
            )
        if report.steps:
            typer.echo(
                f"`raspi host provision {name}` can fix: " + "; ".join(report.steps)
            )
        if not report.fit:
            raise typer.Exit(1)

    @host_app.command("provision")
    def host_provision(
        name: str,
        dry_run: bool = typer.Option(
            False, "--dry-run", help="Show it, change nothing"
        ),
        yes: bool = typer.Option(False, "--yes", help="Skip the confirmation"),
    ) -> None:
        """Install what the Pi lacks (git, curl via apt; the ~/apps folder)."""
        current, _ = live_host(name)
        transport = SshTransport()
        report = doctor(current, transport)
        if not report.ok:
            raise _fail(report.message)
        for step in report.steps:
            typer.echo(f"  - {step}")
        if report.steps and not dry_run and not yes:
            typer.confirm(f"Apply these changes on {name}?", abort=True)
        result = provision(current, transport, dry_run=dry_run)
        if dry_run and result.script:
            typer.echo(f"\nScript run on {name}:\n{result.script}")
        if not result.ok:
            raise _fail(f"✕ {result.message}")
        typer.secho(f"✓ {result.message}", fg="green")

    @host_app.command("install-node")
    def host_install_node(
        name: str,
        major: Annotated[
            str, typer.Argument(help="Node major version, e.g. 22")
        ] = "22",
        dry_run: bool = typer.Option(
            False, "--dry-run", help="Show it, change nothing"
        ),
        yes: bool = typer.Option(False, "--yes", help="Skip the confirmation"),
    ) -> None:
        """Install Node.js on the Pi through nvm (pinned installer, checksum verified)."""
        current, _ = live_host(name)
        transport = SshTransport()
        try:
            steps, _ = plan_node(current, transport, major)
        except RuntimeError_ as exc:
            raise _fail(str(exc)) from exc
        for step in steps:
            typer.echo(f"  - {step}")
        if not dry_run and not yes:
            typer.confirm(f"Apply these changes on {name}?", abort=True)
        ok, message, script = install_node(current, transport, major, dry_run=dry_run)
        if dry_run and script:
            typer.echo(f"\nScript run on {name}:\n{script}")
        if not ok:
            raise _fail(f"✕ {message}")
        typer.secho(f"✓ {message}", fg="green")

    @host_app.command("deploy-key")
    def host_deploy_key(
        name: str,
        app_name: Annotated[str, typer.Argument(metavar="APP")],
        repo: str | None = typer.Option(
            None, "--repo", help="owner/name: also check that GitHub accepts the key"
        ),
        dry_run: bool = typer.Option(
            False, "--dry-run", help="Show it, change nothing"
        ),
        yes: bool = typer.Option(False, "--yes", help="Skip the confirmation"),
    ) -> None:
        """Create the read-only deploy key of APP on the Pi and print its public key."""
        current, _ = live_host(name)
        transport = SshTransport()
        for step in key_steps(app_name, name):
            typer.echo(f"  - {step}")
        if not dry_run and not yes:
            typer.confirm(f"Apply these changes on {name}?", abort=True)
        made = ensure_key(
            current, transport, app_name, dry_run=dry_run, lookup=fetch_github_keys
        )
        if dry_run and made.script:
            typer.echo(f"\nScript run on {name}:\n{made.script}")
        if not made.ok:
            raise _fail(f"✕ {made.message}")
        typer.secho(f"✓ {made.message}", fg="green")
        if dry_run:
            return
        typer.echo(
            f"\nPublic key (add it as a read-only deploy key):\n{made.public_key}"
        )
        if repo is None:
            typer.echo("Check it later with --repo owner/name.")
            return
        checked = verify_repo(current, transport, app_name, repo)
        if not checked.ok:
            raise _fail(f"✕ {checked.message}")
        typer.secho(
            f"✓ {checked.message}  Clone with {clone_url(app_name, repo)}", fg="green"
        )

    @host_app.command("install-app")
    def host_install_app(
        name: str,
        app_name: Annotated[str, typer.Argument(metavar="APP")],
        repo: str = typer.Option(..., "--repo", help="GitHub owner/name"),
        build: str | None = typer.Option(None, "--build", help="Build command"),
        run: str | None = typer.Option(
            None, "--run", help="Start command (for the unit)"
        ),
        path: str | None = typer.Option(None, "--path", help="Folder on the Pi"),
        requires: Annotated[
            list[str] | None,
            typer.Option(
                "--requires",
                help="Prerequisite checked before start: folder|file|mount:PATH (repeatable)",
            ),
        ] = None,
        dry_run: bool = typer.Option(
            False, "--dry-run", help="Show it, change nothing"
        ),
        yes: bool = typer.Option(False, "--yes", help="Skip the confirmation"),
    ) -> None:
        """Clone APP on the Pi through its deploy key, build it, add it to the inventory."""
        entry: dict = {"name": app_name, "unit": f"{app_name}.service", "repo": repo}
        for key, value in (("build", build), ("run", run), ("path", path)):
            if value:
                entry[key] = value
        try:
            if requires:
                parsed = [
                    parse_requirement_line(r.replace(":", " ", 1)) for r in requires
                ]
                entry["requires"] = [{"kind": r.kind, "path": r.path} for r in parsed]
            declared = parse_manifest({"version": 1, "apps": [entry]})[0]
        except ConfigError as exc:
            raise _fail(str(exc)) from exc
        _, live = live_host(name)
        transport = SshTransport()
        try:
            plan = plan_install(live, transport, declared)
        except InstallError as exc:
            raise _fail(str(exc)) from exc
        for step in plan.steps:
            typer.echo(f"  - {step}")
        if dry_run:
            typer.echo(f"\nScript run on {name}:\n{plan.script}")
            typer.echo("Dry run: nothing was changed.")
            return
        if not yes:
            typer.confirm(f"Apply these changes on {name}?", abort=True)
        result = install_app(live, transport, plan)
        if not result.ok:
            raise _fail(f"✕ {result.message}")
        typer.secho(f"✓ {result.message}", fg="green")

    @host_app.command("install-unit")
    def host_install_unit(
        name: str,
        app_name: Annotated[str, typer.Argument(metavar="APP")],
        dry_run: bool = typer.Option(
            False, "--dry-run", help="Show it, change nothing"
        ),
        yes: bool = typer.Option(False, "--yes", help="Skip the confirmation"),
    ) -> None:
        """Create and enable the systemd unit of APP from its `run` command (not started)."""
        _, live = live_host(name)
        declared = next((a for a in live.apps if a.name == app_name), None)
        if declared is None:
            raise _fail(f"{app_name} is not in the inventory of {name}")
        transport = SshTransport()
        try:
            plan = plan_unit(live, transport, declared)
        except UnitError as exc:
            raise _fail(str(exc)) from exc
        for step in plan.steps:
            typer.echo(f"  - {step}")
        for warning in plan.warnings:
            typer.secho(f"  ! {warning}", fg="yellow")
        if dry_run:
            typer.echo("")
            typer.echo(f"Unit file:{chr(10)}{plan.text}")
            typer.echo("Dry run: nothing was changed.")
            return
        if not yes:
            typer.confirm(f"Apply these changes on {name}?", abort=True)
        result = install_unit(live, transport, plan)
        if not result.ok:
            raise _fail(f"✕ {result.message}")
        typer.secho(f"✓ {result.message}", fg="green")
