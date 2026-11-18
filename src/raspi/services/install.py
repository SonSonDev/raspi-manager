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
from raspi.core.unit import UnitError
from raspi.services.adopt import add_apps, update_app
from raspi.services.unit import UnitPlan, install_unit, plan_unit
from raspi.ssh.transport import Transport

MIN_FREE_MB = 500


@dataclass
class InstallPlan:
    app: AppRef
    state: str  # "missing" (will clone) or "repo" (already cloned)
    steps: list[str]
    script: str
    unit: UnitPlan | None = (
        None  # offered when run, folder or env files of a known app change
    )


def _unit_fields(app: AppRef) -> tuple:
    return (app.run, app.path, app.env_files)


def _plan_unit_update(
    host: Host, transport: Transport, known: AppRef | None, wanted: AppRef
) -> UnitPlan | None:
    """The unit of ours that the new declaration would change, else None (read-only).

    Nothing is offered for a new app, for a change that does not reach the unit, or when
    there is no unit of ours to replace (a missing unit is “Install unit…”'s job, a foreign
    one is never touched).
    """
    if known is None or not wanted.run or _unit_fields(known) == _unit_fields(wanted):
        return None
    try:
        plan = plan_unit(host, transport, replace(wanted, env_files=known.env_files))
    except UnitError:
        return None
    return plan if plan.existing else None


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
    known = next((a for a in host.apps if a.name == app.name), None)
    return InstallPlan(
        app,
        state,
        install_steps(app, state),
        build_install_script(app, state),
        _plan_unit_update(host, transport, known, app),
    )


@dataclass
class InstallResult:
    ok: bool
    message: str
    rev: str = ""


def install_app(
    host: Host,
    transport: Transport,
    plan: InstallPlan,
    register: bool = True,
    with_unit: bool = False,
) -> InstallResult:
    """Clone (when missing) and build, then add the app to the Pi's apps.yaml if absent.

    With `with_unit` and a planned unit, the unit is rewritten last, after the inventory:
    it is never restarted here.
    """
    run = transport.run_script(host, plan.script, timeout=1800)
    if not run.ok:
        return InstallResult(False, f"Install failed on {host.name}: {run.error}")
    rev = next((ln[4:] for ln in run.stdout.splitlines() if ln.startswith("rev=")), "")
    message = f"{plan.app.name} is built on {host.name}" + (f" at {rev}" if rev else "")
    if register:
        known = next((a for a in host.apps if a.name == plan.app.name), None)
        # the form knows nothing about env files: keep the ones already declared
        wanted = replace(
            plan.app,
            env_files=known.env_files if known else plan.app.env_files,
            built=rev if plan.app.build and rev else None,
        )
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
    if with_unit and plan.unit:
        written = install_unit(host, transport, plan.unit)
        if not written.ok:
            return InstallResult(
                False,
                f"{message}, but the unit was not updated: {written.message}",
                rev,
            )
        message += "; the unit was rewritten (the app was not restarted)"
    return InstallResult(True, message, rev)
