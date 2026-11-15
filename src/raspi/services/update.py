"""Update an installed app to another revision (or roll back), rebuild it, restart it."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from raspi.core.models import AppRef, Host
from raspi.core.update import (
    UpdateError,
    build_probe_script,
    build_update_script,
    check_ref,
    parse_probe,
    rollback_target,
    update_steps,
)
from raspi.services.actions import run_action
from raspi.services.adopt import update_app
from raspi.ssh.transport import Transport


@dataclass
class UpdatePlan:
    app: AppRef
    old: str  # full hash now checked out
    new: str  # full hash to check out
    commits: list[str]  # "abc1234 subject" lines of what comes in (names only, no diff)
    steps: list[str]
    script: str
    restart: bool
    rollback: bool
    warnings: list[str] = field(default_factory=list)

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
    notes = []
    restart = facts.active == "active" and facts.load != "not-found"
    if restart and not facts.has_sudo:
        restart = False
        notes.append("No passwordless sudo: the app will not be restarted")
    if facts.load == "not-found":
        notes.append("The app has no unit yet: nothing to restart")
    elif facts.active != "active":
        notes.append("The app is not running: it stays stopped")
    steps = update_steps(app, facts.head, facts.target, facts.behind, restart, rollback)
    return UpdatePlan(
        app,
        facts.head,
        facts.target,
        facts.commits,
        steps,
        build_update_script(app, facts.target),
        restart,
        rollback,
        notes,
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
    if plan.restart:
        restarted = run_action(host, app, "restart", transport)
        if not restarted.ok:
            return UpdateResult(
                False, messages + [f"Restart failed: {restarted.message}"]
            )
        messages.append(f"Restarted ({restarted.state})")
    return UpdateResult(True, messages)
