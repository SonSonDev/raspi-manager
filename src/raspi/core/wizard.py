"""The steps of setting an app up, and how far one has come (pure; no I/O).

Done-ness is read from the Pi's status and the app's declared fields every time, so the wizard
can be closed at any point and reopened where it stopped. Only the first step that is not done is
actionable ("current"); the ones after it wait.
"""

from __future__ import annotations

from dataclasses import dataclass

from raspi.core.models import AppRef, AppStatus
from raspi.core.stage import same_rev

KEY, INSTALL, ENV, UNIT, START = "key", "install", "env", "unit", "start"
LABELS = {
    KEY: "Deploy key",
    INSTALL: "Clone and build",
    ENV: "Environment",
    UNIT: "Systemd unit",
    START: "Start",
}
ORDER = (KEY, INSTALL, ENV, UNIT, START)
DONE, CURRENT, WAITING = "done", "current", "waiting"
GLYPH = {DONE: "✓", CURRENT: "▶", WAITING: "○"}


@dataclass(frozen=True)
class Step:
    key: str
    label: str
    state: str  # done | current | waiting
    detail: str = ""


def _key_detail(cloned: bool, verified: bool, status: AppStatus) -> str:
    if cloned or verified:
        return "accepted by GitHub" if verified else "used by the clone"
    if status.key_present:
        return "created, not checked with GitHub yet"
    return "not created yet"


def _done(
    app: AppRef, status: AppStatus, skipped: frozenset[str], verified: bool
) -> dict[str, tuple[bool, str]]:
    cloned = bool(status.rev)
    built = cloned and (not app.build or same_rev(app.built or "", status.rev))
    declared = len(app.env_files)
    present = sum(status.env_present[:declared])
    if ENV in skipped:
        env = (True, "skipped: the app needs none")
    elif not declared:
        env = (False, "no environment file declared yet")
    elif present == declared:
        env = (True, f"{declared} file(s) on the Pi")
    else:
        env = (False, f"{declared - present} declared file(s) not on the Pi yet")
    if built:
        install = (True, f"at {status.rev}" + (", built" if app.build else ""))
    elif cloned:
        install = (False, f"cloned at {status.rev}, not built yet")
    else:
        install = (False, "not cloned yet")
    return {
        KEY: (cloned or verified, _key_detail(cloned, verified, status)),
        INSTALL: install,
        ENV: env,
        UNIT: (
            status.state != "no-unit",
            "exists" if status.state != "no-unit" else "not created yet",
        ),
        START: (
            status.state == "running",
            "running" if status.state == "running" else "not running",
        ),
    }


def wizard_steps(
    app: AppRef,
    status: AppStatus,
    skipped: frozenset[str] = frozenset(),
    verified: bool = False,
) -> list[Step]:
    """`verified`: GitHub accepted the deploy key during this session (the key file alone is not proof)."""
    facts = _done(app, status, skipped, verified)
    steps, current_seen = [], False
    for key in ORDER:
        done, detail = facts[key]
        if done:
            state = DONE
        elif not current_seen:
            state, current_seen = CURRENT, True
        else:
            state = WAITING
        steps.append(Step(key, LABELS[key], state, detail))
    return steps


def current_step(steps: list[Step]) -> Step | None:
    return next((s for s in steps if s.state == CURRENT), None)
