"""Load ~/.raspi/hosts.yaml. Values end up in remote commands, so they are validated."""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

from raspi.core.deploykey import DeployKeyError, check_repo
from raspi.core.models import AppRef, EnvFile, Host

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_UNIT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]*\.service$")
_PATH = re.compile(r"^/[A-Za-z0-9._/-]+$")
_ADDR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.:-]*$")
_USER = re.compile(r"^[a-z_][a-z0-9_-]*$")


class ConfigError(Exception):
    """Human-readable configuration problem."""


class HostsFileMissing(ConfigError):
    """hosts.yaml does not exist yet: a first-run state, not a broken config."""


def raspi_home() -> Path:
    return Path(os.environ.get("RASPI_HOME") or Path.home() / ".raspi")


def _check(pattern: re.Pattern[str], value: object, what: str) -> str:
    if not isinstance(value, str) or not pattern.match(value):
        raise ConfigError(f"Invalid {what}: {value!r}")
    return value


_SYSTEM_DIRS = (
    "/etc/",
    "/usr/",
    "/proc/",
    "/sys/",
    "/boot/",
    "/bin/",
    "/sbin/",
    "/lib/",
    "/var/",
)


def _env_path(value: object) -> str:
    path = _check(_PATH, value, "env_file")
    if path.startswith(_SYSTEM_DIRS) or ".." in path.split("/"):
        raise ConfigError(
            f"env_file must be in a user-writable folder (not a system path): {path}"
        )
    return path


def check_env_path(value: str) -> str:
    """Validate an env file path typed by the user (raises ConfigError)."""
    return _env_path(value)


def check_host(name: str, address: str, user: str) -> tuple[str, str, str]:
    """Validate a Pi typed by the user (these values end up in ssh arguments)."""
    return (
        _check(_NAME, name, "host name (letters, digits, . _ -)"),
        _check(_ADDR, address, "address (hostname or IP)"),
        _check(_USER, user, "user (lowercase letters, digits, _ -)"),
    )


def check_label(value: str) -> str:
    return _check(_NAME, value, "label (letters, digits, . _ -)")


def _env_files(app: dict) -> tuple[EnvFile, ...]:
    """`env_file: /path` (one file) and/or `env_files: [{label, path}, ...]`."""
    files = []
    if app.get("env_file"):
        files.append(EnvFile("default", _env_path(app["env_file"])))
    for item in app.get("env_files") or []:
        if not isinstance(item, dict):
            raise ConfigError("env_files entries must be {label: ..., path: ...}")
        files.append(
            EnvFile(
                _check(_NAME, item.get("label"), "env file label"),
                _env_path(item.get("path")),
            )
        )
    if len({f.label for f in files}) != len(files):
        raise ConfigError(f"Duplicate env file label in app {app.get('name')!r}")
    return tuple(files)


MANIFEST_VERSION = 1
_MAX_COMMAND = 500


def check_repo_field(value: object) -> str:
    try:
        return check_repo(str(value))
    except DeployKeyError as exc:
        raise ConfigError(f"Invalid repo: {value!r} ({exc})") from exc


def check_command(value: object, what: str) -> str:
    """One-line shell command typed by the developer; it runs on the Pi, so it is shown first."""
    text = value.strip() if isinstance(value, str) else ""
    if not text or len(text) > _MAX_COMMAND or any(ord(c) < 32 for c in text):
        raise ConfigError(
            f"Invalid {what}: one line of at most {_MAX_COMMAND} characters"
        )
    return text


def _apps(items: object) -> tuple[AppRef, ...]:
    if not isinstance(items, list) or not all(isinstance(a, dict) for a in items):
        raise ConfigError("'apps' must be a list of {name, unit, ...} entries")
    apps = tuple(
        AppRef(
            name=_check(_NAME, a.get("name"), "app name"),
            unit=_check(_UNIT, a.get("unit"), "unit (must end with .service)"),
            path=_check(_PATH, a["path"], "app path") if a.get("path") else None,
            env_files=_env_files(a),
            repo=check_repo_field(a["repo"]) if a.get("repo") else None,
            build=check_command(a["build"], "build command")
            if a.get("build")
            else None,
            run=check_command(a["run"], "run command") if a.get("run") else None,
        )
        for a in items
    )
    if len({a.name for a in apps}) != len(apps):
        raise ConfigError("Duplicate app name in the apps list")
    return apps


def parse_manifest(data: object) -> tuple[AppRef, ...]:
    """Apps listed in a Pi's ~/.raspi/apps.yaml (already loaded; the Pi is untrusted input)."""
    if not isinstance(data, dict) or data.get("version") != MANIFEST_VERSION:
        raise ConfigError(f"apps.yaml must start with 'version: {MANIFEST_VERSION}'")
    return _apps(data.get("apps") or [])


def parse_hosts(data: object) -> list[Host]:
    if not isinstance(data, dict) or not isinstance(data.get("hosts"), list):
        raise ConfigError("hosts.yaml must contain a 'hosts:' list")
    hosts = []
    for h in data["hosts"]:
        apps = _apps(h.get("apps") or [])
        hosts.append(
            Host(
                name=_check(_NAME, h.get("name"), "host name"),
                address=_check(_ADDR, h.get("address"), "host address"),
                user=_check(_USER, h.get("user"), "user"),
                apps=apps,
            )
        )
    return hosts


def load_hosts(path: Path | None = None) -> list[Host]:
    path = path or raspi_home() / "hosts.yaml"
    if not path.exists():
        raise HostsFileMissing(
            f"{path} not found. Add a Pi from the app (Add a Pi…) "
            "or run: raspi host add NAME ADDRESS USER"
        )
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [] if data is None else parse_hosts(data)  # an empty file means no Pi yet
