"""The processes of an app (pure). An app without a `processes:` list has one, called `main`."""

from __future__ import annotations

from raspi.core.models import AppRef, Process

MAIN = "main"


def all_processes(app: AppRef) -> tuple[Process, ...]:
    """Declared processes, else the app's own `unit` and `run` as the process `main`."""
    return app.processes or (Process(MAIN, app.unit, app.run),)
