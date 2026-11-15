"""App prerequisites: what must exist on the Pi before the app starts (pure; no I/O).

They are declared in the app's `requires:` list, checked read-only with the live status and reported
by name. raspi never creates them: a data folder that is not there may mean an unmounted disk, and
creating it would write on the SD card instead.
"""

from __future__ import annotations

from raspi.core.models import AppRef, AppStatus, Requirement

WORDS = {"folder": "folder", "file": "file", "mount": "mounted disk"}


def describe(req: Requirement) -> str:
    return f"{WORDS[req.kind]} {req.path}"


def missing_requirements(app: AppRef, status: AppStatus | None) -> list[Requirement]:
    """Requirements the last status found unsatisfied (unknown ones are not reported)."""
    if status is None:
        return []
    return [
        req
        for i, req in enumerate(app.requires)
        if i < len(status.requires_present) and not status.requires_present[i]
    ]


def missing_lines(app: AppRef, status: AppStatus | None) -> list[str]:
    return [
        f"Missing on the Pi: {describe(r)}" for r in missing_requirements(app, status)
    ]
