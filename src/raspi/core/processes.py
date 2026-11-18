"""The processes of an app (pure). An app without a `processes:` list has one, called `main`."""

from __future__ import annotations

from raspi.core.models import AppRef, Process

MAIN = "main"


def format_process_line(app: AppRef, process: Process) -> str:
    """The text a person types for this process (the inverse of `parse_process_line`)."""
    unit = (
        ""
        if process.unit == f"{app.name}-{process.name}.service"
        else f"={process.unit}"
    )
    rule = "" if process.on_update == "restart" else f":{process.on_update}"
    return f"{process.name}{unit}:{process.run or ''}{rule}"


def all_processes(app: AppRef) -> tuple[Process, ...]:
    """Declared processes, else the app's own `unit` and `run` as the process `main`."""
    return app.processes or (Process(MAIN, app.unit, app.run),)
