"""Where an app is on the path of WORKFLOW.md, and what to do next (pure; no I/O).

Everything is read from the Pi's last status (never cached as truth) and from the app's declared
fields. The answer names the button that moves the app one step forward; it is a hint, not a gate:
every button stays usable when its own prerequisite holds.
"""

from __future__ import annotations

from dataclasses import dataclass

from raspi.core.models import AppRef, AppStatus, Process, ProcessStatus
from raspi.core.requires import describe, missing_requirements
from raspi.core.route_health import down_routes, route_summary
from raspi.core.unit import process_drift, unit_drift

READY = "✓ Ready"


@dataclass(frozen=True)
class Next:
    label: str  # shown in the table
    why: str = ""  # shown as a tooltip
    done: bool = False


def same_rev(a: str, b: str) -> bool:
    """Short revisions can differ in length: compare as prefixes."""
    return bool(a and b) and (a.startswith(b) or b.startswith(a))


def _app_blocker(app: AppRef, status: AppStatus) -> Next | None:
    """What stops the app as a whole (key, clone, build, run command, env files)."""
    if app.repo:
        cloned = bool(status.rev)
        if not cloned:
            if status.key_present is False:
                return Next(
                    "Deploy key…",
                    "The repository cannot be cloned without a deploy key",
                )
            return Next("Install app…", "Clone the repository and build it")
        if app.build and not same_rev(app.built or "", status.rev):
            why = (
                "The working copy changed since the last build"
                if app.built
                else "The app has not been built yet"
            )
            return Next("Build (Install app…)", why)
        if not app.run:
            return Next(
                "Set the run command (Install app…)", "A unit needs a run command"
            )
        missing = [
            f.label
            for f, present in zip(app.env_files, status.env_present, strict=False)
            if not present
        ]
        if missing:
            return Next(
                "Push environment…",
                "Missing on the Pi: " + ", ".join(missing),
            )
    return None


def next_step(app: AppRef, status: AppStatus | None) -> Next:
    if status is None:
        return Next("")
    state = status.state
    blocker = _app_blocker(app, status)
    if blocker:
        return blocker
    if state == "no-unit":
        if app.repo and not app.run:
            return Next(
                "Set the run command (Install app…)", "A unit needs a run command"
            )
        if app.repo and not app.env_files:
            return Next(
                "Environment…",
                "Declare its environment file (go straight to Install unit… if the "
                "app needs none)",
            )
        return Next("Install unit…", "The app has no systemd unit yet")
    unitless = [p.name for p in status.processes if p.state == "no-unit"]
    if unitless:
        return Next("Install unit…", "No systemd unit yet for: " + ", ".join(unitless))
    drift = unit_drift(app, status)
    if drift:
        return Next(
            "Unit out of date (Install unit…)",
            "The unit differs from the inventory: "
            + ", ".join(drift)
            + ". Install unit… rewrites it; the app is not restarted.",
        )
    idle = [p.name for p in status.processes if p.state in ("stopped", "failed")]
    if state == "running" and idle:
        return Next("Start", "Not running: " + ", ".join(idle))
    if state in ("stopped", "failed"):
        absent = missing_requirements(app, status)
        if absent:
            return Next(
                "Prerequisite missing",
                "Before starting: " + ", ".join(describe(r) for r in absent),
            )
        return Next("Start", "The unit exists and is not running")
    if state == "running":
        if down_routes(app, status):
            return Next("Route not answering", route_summary(app, status))
        return Next(READY, done=True, why=route_summary(app, status))
    return Next("")


def process_next(
    app: AppRef, status: AppStatus | None, process: Process, live: ProcessStatus | None
) -> Next:
    """The next step of one row of an app with a `processes:` list."""
    if status is None or live is None:
        return Next("")
    blocker = _app_blocker(app, status)
    if blocker:
        return blocker
    if live.state == "no-unit":
        return Next("Install unit…", f"{process.unit} does not exist yet")
    drift = process_drift(app, process, live)
    if drift:
        return Next(
            "Unit out of date (Install unit…)",
            "The unit differs from the inventory: "
            + ", ".join(drift)
            + ". Install unit… rewrites it; the process is not restarted.",
        )
    if live.state in ("stopped", "failed"):
        return Next("Start", f"{process.unit} exists and is not running")
    if live.state == "running":
        if live.started and status.head_time and live.started < status.head_time:
            return Next(
                "Old code (Restart)",
                "Started before the last update, so it still runs the old code: "
                "restart it when you are ready.",
            )
        return Next(READY, done=True)
    return Next("")
