"""Render a Pi's app inventory (`~/.raspi/apps.yaml`). Pure; no secrets, only paths."""

from __future__ import annotations

import yaml

from raspi.core.models import AppRef

VERSION = 1
HEADER = (
    "# App inventory of this Raspberry Pi, kept by raspi-manager.\n"
    "# Paths and names only: environment variable values never go in this file.\n"
)


def render_manifest(apps: tuple[AppRef, ...]) -> str:
    entries = []
    for app in apps:
        entry: dict = {"name": app.name, "unit": app.unit}
        if app.path:
            entry["path"] = app.path
        for field in ("repo", "build", "run", "built", "previous"):
            if getattr(app, field):
                entry[field] = getattr(app, field)
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
