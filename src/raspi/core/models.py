"""Plain data models shared by every layer. No I/O here."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class EnvFile:
    label: str  # "default" for the common single-file case
    path: str  # absolute, user-writable path on the Pi


@dataclass(frozen=True)
class AppRef:
    name: str
    unit: str
    path: str | None = None  # git working copy on the Pi, if the app has one
    env_files: tuple[
        EnvFile, ...
    ] = ()  # environment files the app (or its build) reads
    repo: str | None = None  # GitHub `owner/name`, cloned through the app's deploy key
    build: str | None = None  # shell command run in the working copy after a checkout
    run: str | None = None  # command the app's systemd unit starts


@dataclass(frozen=True)
class Host:
    name: str
    address: str
    user: str
    apps: tuple[AppRef, ...] = ()


@dataclass
class AppStatus:
    name: str
    unit: str
    load: str = ""  # systemd LoadState ("not-found": the unit file does not exist)
    active: str = "unknown"  # systemd ActiveState
    sub: str = ""  # systemd SubState
    enabled: str = ""  # systemd UnitFileState
    pid: int = 0
    since: str = ""
    restarts: int = 0
    rev: str = ""
    dirty: int | None = None  # None = not a git repo
    remote: str = ""

    @property
    def state(self) -> str:
        """Collapse systemd states into the UI states (see docs/DESIGN.md)."""
        if self.load == "not-found":
            return "no-unit"
        if self.active == "active":
            return "running"
        if self.active == "failed":
            return "failed"
        if self.active in ("activating", "deactivating", "reloading"):
            return "working"
        if self.active == "inactive":
            return "stopped"
        return "unknown"


@dataclass
class HostStatus:
    host: Host
    checked_at: datetime
    reachable: bool = True
    error: str = ""
    apps: list[AppStatus] = field(default_factory=list)
    inventory: str = (
        "pc"  # where `host.apps` came from: "pi" (apps.yaml) or "pc" (hosts.yaml)
    )
    inventory_error: str = ""  # why the Pi's apps.yaml was ignored, if it was
