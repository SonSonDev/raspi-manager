"""Move an app and its data from one Pi to another, relayed through this PC.

Order: probe both sides (read-only) → stop the source → copy each data folder and env file →
compare checksums → start the target → disable the source unit. Any failure before the target
starts restarts the source and empties what was copied. The source data is never deleted.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace

from raspi.core.models import AppRef, Host
from raspi.core.move import (
    RUNNING,
    MoveError,
    SideFacts,
    build_cleanup_script,
    build_disable_script,
    build_manifest_script,
    build_pack_script,
    build_probe_script,
    build_unpack_script,
    check_move,
    data_folders,
    move_steps,
    parse_probe,
)
from raspi.core.remote import (
    build_env_push_script,
    build_env_read_script,
    parse_env_read,
)
from raspi.services.actions import run_action
from raspi.ssh.transport import ByteTransport

Progress = Callable[[str], None]


@dataclass
class MovePlan:
    app: AppRef  # the source's entry: data folders and env files come from it
    target_app: AppRef
    source: Host
    target: Host
    source_facts: SideFacts
    steps: list[str]
    warnings: list[str] = field(default_factory=list)
    scripts: str = ""  # what runs, for the confirmation's details

    @property
    def was_running(self) -> bool:
        return self.source_facts.state in RUNNING


@dataclass
class MoveResult:
    ok: bool
    message: str
    started: bool = False


def _probe(host: Host, transport: ByteTransport, app: AppRef) -> SideFacts:
    result = transport.run_script(host, build_probe_script(app), timeout=40)
    if not result.ok:
        raise MoveError(f"Cannot inspect {host.name}: {result.error}")
    return parse_probe(result.stdout, app)


def plan_move(
    source: Host, target: Host, app_name: str, transport: ByteTransport
) -> MovePlan:
    """Read-only. Raises MoveError, listing every reason, when the move must not start."""
    if source.name == target.name:
        raise MoveError("The source and the target are the same Pi")
    app = next((a for a in source.apps if a.name == app_name), None)
    if app is None:
        raise MoveError(f"Unknown app '{app_name}' on {source.name}")
    target_app = next((a for a in target.apps if a.name == app_name), None)
    if target_app is None:
        raise MoveError(
            f"{app_name} is not in the inventory of {target.name}: set it up there first "
            "(App → New app…, up to the unit; do not start it)"
        )
    src = _probe(source, transport, app)
    # the target is read with the source's declaration: same paths on both sides
    tgt = _probe(target, transport, replace_unit(app, target_app))
    refuse, warn = check_move(app, src, tgt, target.name)
    if src.state == "missing":
        refuse.append(f"{app.name} has no unit on {source.name}")
    if refuse:
        raise MoveError("\n".join(f"• {r}" for r in refuse))
    steps = move_steps(app, source.name, target.name, src.state in RUNNING)
    scripts = "\n".join(
        [
            *(
                f"# pack on {source.name}\n{build_pack_script(r.path)}"
                for r in data_folders(app)
            ),
            *(
                f"# unpack on {target.name}\n{build_unpack_script(r.path)}"
                for r in data_folders(app)
            ),
            f"# disable on {source.name}\n{build_disable_script(app.unit)}",
        ]
    )
    return MovePlan(app, target_app, source, target, src, steps, warn, scripts)


def replace_unit(app: AppRef, target_app: AppRef) -> AppRef:
    """The source's declaration with the target's unit and working copy (they may differ)."""
    return replace(app, unit=target_app.unit, path=target_app.path)


def _manifest(host: Host, transport: ByteTransport, path: str) -> str:
    result = transport.run_script(host, build_manifest_script(path), timeout=300)
    if not result.ok:
        raise MoveError(
            f"Cannot read the checksums of {path} on {host.name}: {result.error}"
        )
    return result.stdout


def apply_move(
    plan: MovePlan, transport: ByteTransport, progress: Progress = lambda _: None
) -> MoveResult:
    app, source, target = plan.app, plan.source, plan.target
    copied: list[str] = []
    stopped = False

    def abort(reason: str) -> MoveResult:
        notes = []
        for path in copied:
            cleaned = transport.run_script(target, build_cleanup_script(path))
            if not cleaned.ok:
                notes.append(
                    f"could not empty {path} on {target.name}: {cleaned.error}"
                )
        if stopped and plan.was_running:
            back = run_action(source, app, "start", transport)
            notes.append(
                f"{app.name} restarted on {source.name}"
                if back.ok
                else f"RESTART {source.name} BY HAND: {back.message}"
            )
        return MoveResult(False, "\n".join([reason, *notes]))

    try:
        if plan.was_running:
            progress(f"Stopping {app.name} on {source.name}…")
            done = run_action(source, app, "stop", transport)
            stopped = True  # a failed stop may still have stopped it: restart in doubt
            if not done.ok or done.state in RUNNING:
                return abort(
                    f"Could not stop {app.name} on {source.name}: {done.message}"
                )
        for req, size in zip(data_folders(app), plan.source_facts.sizes):
            if size < 0:
                continue  # absent on the source: warned in the plan, nothing to carry
            progress(f"Copying {req.path}…")
            before = _manifest(source, transport, req.path)
            packed = transport.run_bytes(
                source, build_pack_script(req.path), timeout=1800
            )
            if not packed.ok:
                return abort(f"Cannot read {req.path} on {source.name}: {packed.error}")
            copied.append(req.path)
            sent = transport.run_bytes(
                target, build_unpack_script(req.path), packed.stdout, timeout=1800
            )
            if not sent.ok:
                return abort(f"Cannot write {req.path} on {target.name}: {sent.error}")
            progress(f"Verifying {req.path}…")
            if _manifest(target, transport, req.path) != before:
                return abort(f"Checksums of {req.path} differ after the copy: aborted")
        for env in app.env_files:
            progress(f"Copying {env.path}…")
            read = transport.run_script(source, build_env_read_script(env.path))
            content = parse_env_read(read.stdout) if read.ok else None
            if content is None:
                continue  # no such file on the source: nothing to carry
            pushed = transport.run_script(
                target, build_env_push_script(env.path, content)
            )
            if not pushed.ok:
                return abort(
                    f"Cannot write {env.path} on {target.name}: {pushed.error}"
                )
        if not plan.was_running:
            return MoveResult(
                True, f"Data of {app.name} copied to {target.name}; not started."
            )
        progress(f"Starting {app.name} on {target.name}…")
        started = run_action(target, plan.target_app, "start", transport)
        if not started.ok or started.state not in RUNNING:
            run_action(target, plan.target_app, "stop", transport)
            return abort(
                f"{app.name} did not start on {target.name}: {started.message}"
            )
    except MoveError as exc:
        return abort(str(exc))
    off = transport.run_script(source, build_disable_script(app.unit))
    tail = (
        f"{app.unit} is disabled on {source.name}"
        if off.ok
        else f"disable {app.unit} on {source.name} by hand: {off.error}"
    )
    return MoveResult(
        True,
        f"{app.name} runs on {target.name}; stopped on {source.name}, its data is kept ({tail}).",
        started=True,
    )
