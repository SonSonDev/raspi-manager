"""Load ~/.raspi/hosts.yaml. Values end up in remote commands, so they are validated."""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

from raspi.core.models import AppRef, Host

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_UNIT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]*\.service$")
_PATH = re.compile(r"^/[A-Za-z0-9._/-]+$")
_ADDR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.:-]*$")
_USER = re.compile(r"^[a-z_][a-z0-9_-]*$")


class ConfigError(Exception):
    """Human-readable configuration problem."""


def raspi_home() -> Path:
    return Path(os.environ.get("RASPI_HOME") or Path.home() / ".raspi")


def _check(pattern: re.Pattern[str], value: object, what: str) -> str:
    if not isinstance(value, str) or not pattern.match(value):
        raise ConfigError(f"Invalid {what}: {value!r}")
    return value


def parse_hosts(data: object) -> list[Host]:
    if not isinstance(data, dict) or not isinstance(data.get("hosts"), list):
        raise ConfigError("hosts.yaml must contain a 'hosts:' list")
    hosts = []
    for h in data["hosts"]:
        apps = tuple(
            AppRef(
                name=_check(_NAME, a.get("name"), "app name"),
                unit=_check(_UNIT, a.get("unit"), "unit (must end with .service)"),
                path=_check(_PATH, a["path"], "app path") if a.get("path") else None,
            )
            for a in h.get("apps", [])
        )
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
        raise ConfigError(
            f"{path} not found. Create it with a 'hosts:' list (see docs/SPEC.md F1)."
        )
    return parse_hosts(yaml.safe_load(path.read_text(encoding="utf-8")))
