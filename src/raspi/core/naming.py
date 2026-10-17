"""Small pure helpers for naming things."""

from __future__ import annotations

import posixpath
import re


def suggest_label(app_folder: str | None, env_path: str, taken: set[str]) -> str:
    """Label for an env file from its folder relative to the app: server/.env -> 'server'."""
    base = "default"
    if app_folder and env_path.startswith(app_folder.rstrip("/") + "/"):
        rel_dir = posixpath.dirname(env_path[len(app_folder.rstrip("/")) + 1 :])
        if rel_dir:
            base = (
                re.sub(r"[^A-Za-z0-9._-]", "-", posixpath.basename(rel_dir))
                or "default"
            )
        elif "default" in taken:
            base = "root"
    elif env_path and "default" in taken:
        base = posixpath.basename(posixpath.dirname(env_path)) or "extra"
    label, n = base, 2
    while label in taken:
        label, n = f"{base}-{n}", n + 1
    return label
