"""Update an installed app to another revision (or roll back), rebuild it, restart it."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from raspi.core.models import AppRef, Host
from raspi.core.update import (
    RECORD_STEP,
    ProcessPlan,
    UpdateError,
    build_probe_script,
    build_update_script,
    check_ref,
    parse_probe,
    plan_processes,
    rollback_target,
    update_steps,
)
from raspi.services.actions import run_unit_action
from raspi.services.adopt import update_app
from raspi.ssh.transport import Transport


@dataclass
class UpdatePlan:
    app: AppRef
    old: str  # full hash now checked out
    new: str  # full hash to check out
    commits: list[str]  # "abc1234 subject" lines of what comes in (names only, no diff)
    head_steps: list[str]  # checkout and build
    script: str
    processes: list[ProcessPlan]
    rollback: bool
    warnings: list[str] = field(default_factory=list)

    @property
    def steps(self) -> list[str]:
        return [*self.head_steps, *(p.step for p in self.processes), RECORD_STEP]

    @property
    def restart(self) -> bool:
        return any(p.restart for p in self.processes)

    @restart.setter
    def restart(self, value: bool) -> None:
        if not value:  # only ever used to cancel every restart
            for process in self.processes:
                process.restart = False

    @property
    def nothing_to_do(self) -> bool:
        built = self.app.built or ""
        built_ok = not self.app.build or self.new.startswith(built) and built
        return self.old == self.new and bool(built_ok)


def plan_update(
    host: Host,
    transport: Transport,
    app: AppRef,
    ref: str = "",
    rollback: bool = False,
) -> UpdatePlan:
    """Reads the Pi (and fetches from GitHub). Raises UpdateError with the reason and the fix."""
    if not app.repo:
        raise UpdateError(
            f"{app.name} has no repo declared: it is not updated from here"
        )
    ref = check_ref(ref)
    first = transport.run_script(host, build_probe_script(app, ref))
    if not first.ok:
        raise UpdateError(f"Cannot inspect {host.name}: {first.error}")
    facts = parse_probe(first.stdout)
    if not facts.workdir_ok:
        raise UpdateError(
            f"{app.name} is not cloned on {host.name}: use “Install app…” first"
        )
    if rollback:
        wanted = rollback_target(app, facts.head)
        if not wanted:
            raise UpdateError(f"{app.name} has no earlier revision to go back to")
        second = transport.run_script(host, build_probe_script(app, sha=wanted))
        if not second.ok:
            raise UpdateError(f"Cannot inspect {host.name}: {second.error}")
        facts = parse_probe(second.stdout)
    if not facts.fetch_ok:
        raise UpdateError(
            f"{host.name} cannot reach GitHub with the deploy key of {app.name}: "
            "check it with “Deploy key…”"
        )
    if not facts.target:
        what = (
            f"revision {rollback_target(app, facts.head)}"
            if rollback
            else f"ref {ref!r}"
        )
        raise UpdateError(f"Unknown {what} in the repository")
    if facts.dirty:
        raise UpdateError(
            f"{facts.dirty} uncommitted change(s) in the working copy of {app.name}: "
            "commit or discard them on the Pi first"
        )
    if facts.local_only:
        raise UpdateError(
            f"{facts.local_only} commit(s) of {app.name} exist only on {host.name} and "
            "would be left behind: push them first"
        )
    processes = plan_processes(app, facts)
    return UpdatePlan(
        app,
        facts.head,
        facts.target,
        facts.commits,
        update_steps(app, facts.head, facts.target, facts.behind, rollback),
        build_update_script(app, facts.target),
        processes,
        rollback,
        [p.note for p in processes if p.note],
    )


@dataclass
class UpdateResult:
    ok: bool
    messages: list[str]


def apply_update(host: Host, transport: Transport, plan: UpdatePlan) -> UpdateResult:
    app = plan.app
    run = transport.run_script(host, plan.script, timeout=1800)
    if not run.ok:
        back = (
            "Use “Update…” with “Roll back” to return to the revision that was built."
            if app.built
            else "Check the working copy on the Pi."
        )
        return UpdateResult(
            False,
            [
                f"Build failed on {host.name} after checking out {plan.new[:7]}: {run.error}",
                f"The app was not restarted and its record is unchanged. {back}",
            ],
        )
    rev = next((ln[4:] for ln in run.stdout.splitlines() if ln.startswith("rev=")), "")
    messages = [f"{app.name} is at {rev or plan.new[:7]} and built"]
    recorded = replace(
        app,
        built=rev if app.build and rev else None,
        previous=None if plan.rollback else plan.old[:7],
    )
    saved = update_app(host, recorded, transport)
    if not saved.ok:
        return UpdateResult(
            False,
            messages + [f"But the new revision was not recorded: {saved.message}"],
        )
    messages.append("Recorded in the Pi's inventory")
    for process in plan.processes:
        if not process.restart:
            if process.kept:
                messages.append(f"{process.unit} was kept running (old code)")
            continue
        restarted = run_unit_action(host, process.unit, "restart", transport)
        if not restarted.ok:
            return UpdateResult(
                False,
                messages + [f"Restart of {process.unit} failed: {restarted.message}"],
            )
        messages.append(
            f"Restarted ({restarted.state})"
            if process.single
            else f"Restarted {process.unit} ({restarted.state})"
        )
    return UpdateResult(True, messages)
