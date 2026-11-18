"""Render a Pi's app inventory (`~/.raspi/apps.yaml`). Pure; no secrets, only paths."""

from __future__ import annotations

import yaml

from raspi.core.models import AppRef, Process

VERSION = 1
HEADER = (
    "# App inventory of this Raspberry Pi, kept by raspi-manager.\n"
    "# Paths and names only: environment variable values never go in this file.\n"
)


def _process(app: AppRef, process: Process) -> dict:
    entry: dict = {"name": process.name, "run": process.run}
    if process.unit != f"{app.name}-{process.name}.service":
        entry["unit"] = process.unit  # an adopted unit keeps its name
    if process.on_update != "restart":
        entry["on_update"] = process.on_update
    return entry


def render_manifest(apps: tuple[AppRef, ...]) -> str:
    entries = []
    for app in apps:
        entry: dict = {"name": app.name, "unit": app.unit}
        if app.path:
            entry["path"] = app.path
        # with processes, unit and run are the first one's: they are written once, there
        for field in ("repo", "build", "run", "built", "previous"):
            if getattr(app, field) and not (app.processes and field == "run"):
                entry[field] = getattr(app, field)
        if app.processes:
            entry["processes"] = [_process(app, p) for p in app.processes]
        if app.requires:
            entry["requires"] = [{"kind": r.kind, "path": r.path} for r in app.requires]
        if app.routes:
            entry["routes"] = [
                {"hostname": r.hostname, "port": r.port, "check": r.check}
                for r in app.routes
            ]
        if app.env_files:
            entry["env_files"] = [
                {"label": f.label, "path": f.path} for f in app.env_files
            ]
        entries.append(entry)
    body = yaml.safe_dump(
        {"version": VERSION, "apps": entries},
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    return HEADER + body
