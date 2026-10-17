"""Edit ~/.raspi/hosts.yaml from the app (declare / forget env files).

The file is re-serialised, so YAML comments and flow-style formatting are lost; a
`hosts.yaml.bak` copy is kept and the result is validated before anything is written.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from raspi.config.loader import (
    ConfigError,
    check_env_path,
    check_label,
    parse_hosts,
    raspi_home,
)


def _hosts_path(path: Path | None) -> Path:
    return path or raspi_home() / "hosts.yaml"


def _find_app(data: dict, host: str, app: str) -> dict:
    for h in data.get("hosts", []):
        if h.get("name") == host:
            for a in h.get("apps", []):
                if a.get("name") == app:
                    return a
            raise ConfigError(f"Unknown app '{app}' on host '{host}'")
    raise ConfigError(f"Unknown host '{host}'")


def _files(app: dict) -> list[dict]:
    """Current env files as [{label, path}], folding the single `env_file` shortcut in."""
    files = []
    if app.get("env_file"):
        files.append({"label": "default", "path": app["env_file"]})
    files.extend(dict(f) for f in app.get("env_files") or [])
    return files


def _store(app: dict, files: list[dict]) -> None:
    app.pop("env_file", None)
    if files:
        app["env_files"] = files
    else:
        app.pop("env_files", None)


def _write(path: Path, data: dict) -> None:
    parse_hosts(data)  # raises ConfigError before we touch the file
    if path.exists():
        backup = path.with_name(path.name + ".bak")
        backup.write_bytes(path.read_bytes())
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        yaml.safe_dump(
            data, sort_keys=False, allow_unicode=True, default_flow_style=False
        ),
        encoding="utf-8",
        newline="\n",
    )
    os.replace(tmp, path)


def _load(path: Path) -> dict:
    if not path.exists():
        raise ConfigError(f"{path} not found")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ConfigError("hosts.yaml must contain a 'hosts:' list")
    return data


def add_env_file(
    host: str, app: str, label: str, env_path: str, path: Path | None = None
) -> None:
    label, env_path = check_label(label), check_env_path(env_path)
    target = _hosts_path(path)
    data = _load(target)
    entry = _find_app(data, host, app)
    files = _files(entry)
    if any(f["label"] == label for f in files):
        raise ConfigError(f"This app already has a file labelled '{label}'")
    if any(f["path"] == env_path for f in files):
        raise ConfigError("This path is already declared for this app")
    files.append({"label": label, "path": env_path})
    _store(entry, files)
    _write(target, data)


def remove_env_file(host: str, app: str, label: str, path: Path | None = None) -> None:
    """Forget a declaration. The file on the Pi and the local copy are left untouched."""
    target = _hosts_path(path)
    data = _load(target)
    entry = _find_app(data, host, app)
    files = [f for f in _files(entry) if f["label"] != label]
    _store(entry, files)
    _write(target, data)
