"""Local copies of each app's environment files (outside git).

~/.raspi/env/<host>.<app>.env for the "default" file, ~/.raspi/env/<host>.<app>.<label>.env otherwise.
"""

from __future__ import annotations

import os
from pathlib import Path

from raspi.config.loader import raspi_home
from raspi.core.env import parse_env, render_env

DEFAULT_LABEL = "default"


def env_path(host: str, app: str, label: str = DEFAULT_LABEL) -> Path:
    suffix = "" if label == DEFAULT_LABEL else f".{label}"
    return raspi_home() / "env" / f"{host}.{app}{suffix}.env"


def load_local(
    host: str, app: str, label: str = DEFAULT_LABEL
) -> dict[str, str] | None:
    """None when no local copy exists yet."""
    path = env_path(host, app, label)
    if not path.exists():
        return None
    return parse_env(path.read_text(encoding="utf-8")).values


def save_local(
    host: str, app: str, values: dict[str, str], label: str = DEFAULT_LABEL
) -> Path:
    path = env_path(host, app, label)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(render_env(values), encoding="utf-8", newline="\n")
    try:
        os.chmod(tmp, 0o600)  # best effort: only meaningful on POSIX
    except OSError:
        pass
    tmp.replace(path)
    return path


def local_copies(host: str, app: str, labels: list[str]) -> list[Path]:
    """Existing local copies for the given labels (and the default one)."""
    wanted = dict.fromkeys([DEFAULT_LABEL, *labels])
    return [p for p in (env_path(host, app, label) for label in wanted) if p.exists()]


def delete_local(host: str, app: str, labels: list[str]) -> int:
    """Remove the local copies; returns how many files went."""
    found = local_copies(host, app, labels)
    for path in found:
        path.unlink()
    return len(found)
