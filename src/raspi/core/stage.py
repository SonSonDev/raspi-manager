"""Where an app is on the path of WORKFLOW.md, and what to do next (pure; no I/O).

Everything is read from the Pi's last status (never cached as truth) and from the app's declared
fields. The answer names the button that moves the app one step forward; it is a hint, not a gate:
every button stays usable when its own prerequisite holds.
"""

from __future__ import annotations

from dataclasses import dataclass

from raspi.core.models import AppRef, AppStatus

READY = "✓ Ready"


@dataclass(frozen=True)
class Next:
    label: str  # shown in the table
    why: str = ""  # shown as a tooltip
    done: bool = False


def _same_rev(a: str, b: str) -> bool:
    """Short revisions can differ in length: compare as prefixes."""
    return bool(a and b) and (a.startswith(b) or b.startswith(a))


def next_step(app: AppRef, status: AppStatus | None) -> Next:
    if status is None:
        return Next("")
    state = status.state
    if app.repo:
        cloned = bool(status.rev)
        if not cloned:
            if status.key_present is False:
                return Next(
                    "Deploy key…",
                    "The repository cannot be cloned without a deploy key",
                )
            return Next("Install app…", "Clone the repository and build it")
        if app.build and not _same_rev(app.built or "", status.rev):
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
    if state in ("stopped", "failed"):
        return Next("Start", "The unit exists and is not running")
    if state == "running":
        return Next(READY, done=True)
    return Next("")
