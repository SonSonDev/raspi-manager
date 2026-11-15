"""Clone an app on a Pi, build it, and record it in the Pi's app inventory."""

from __future__ import annotations

from dataclasses import dataclass, replace

from raspi.core.install import (
    InstallError,
    build_install_script,
    build_probe_script,
    install_steps,
    parse_probe,
)
from raspi.core.models import AppRef, Host
from raspi.services.adopt import add_apps, update_app
from raspi.ssh.transport import Transport

MIN_FREE_MB = 500


@dataclass
class InstallPlan:
    app: AppRef
    state: str  # "missing" (will clone) or "repo" (already cloned)
    steps: list[str]
    script: str


def plan_install(host: Host, transport: Transport, app: AppRef) -> InstallPlan:
    """Read-only. Raises InstallError when the app cannot be installed as declared."""
    probe = transport.run_script(host, build_probe_script(app))
    if not probe.ok:
        raise InstallError(f"Cannot inspect {host.name}: {probe.error}")
    facts = parse_probe(probe.stdout)
    state = facts.get("state", "")
    if facts.get("key") != "yes" and state == "missing":
        raise InstallError(
            f"No deploy key for {app.name} on {host.name}: run "
            f"`raspi host deploy-key {host.name} {app.name} --repo {app.repo}` first"
        )
    free = facts.get("disk_mb", "")
    if free.isdigit() and int(free) < MIN_FREE_MB:
        raise InstallError(f"Only {free} MB free on {host.name}")
    return InstallPlan(
        app, state, install_steps(app, state), build_install_script(app, state)
    )


@dataclass
class InstallResult:
    ok: bool
    message: str
    rev: str = ""


def install_app(
    host: Host, transport: Transport, plan: InstallPlan, register: bool = True
) -> InstallResult:
    """Clone (when missing) and build, then add the app to the Pi's apps.yaml if absent."""
    run = transport.run_script(host, plan.script, timeout=1800)
    if not run.ok:
        return InstallResult(False, f"Install failed on {host.name}: {run.error}")
    rev = next((ln[4:] for ln in run.stdout.splitlines() if ln.startswith("rev=")), "")
    message = f"{plan.app.name} is built on {host.name}" + (f" at {rev}" if rev else "")
    if register:
        known = next((a for a in host.apps if a.name == plan.app.name), None)
        # the form knows nothing about env files: keep the ones already declared
        wanted = replace(plan.app, env_files=known.env_files) if known else plan.app
        if known != wanted:
            if known is None:
                saved, note = add_apps(host, (wanted,), transport), "added to"
            else:
                saved, note = update_app(host, wanted, transport), "updated in"

            if not saved.ok:
                return InstallResult(
                    False, f"{message}, but not recorded: {saved.message}", rev
                )
            message += f"; {note} the inventory"
    return InstallResult(True, message, rev)
