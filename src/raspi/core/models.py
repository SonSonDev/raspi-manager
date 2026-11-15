"""Plain data models shared by every layer. No I/O here."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class EnvFile:
    label: str  # "default" for the common single-file case
    path: str  # absolute, user-writable path on the Pi


REQUIREMENT_KINDS = ("folder", "file", "mount")


@dataclass(frozen=True)
class Requirement:
    """Something that must already exist on the Pi before the app starts (never created by raspi)."""

    kind: str  # folder | file | mount
    path: str  # absolute


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
    built: str | None = None  # git revision the last successful build was made from
    previous: str | None = (
        None  # revision built before the last update (rollback target)
    )
    requires: tuple[Requirement, ...] = ()  # checked read-only, never created


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
    key_present: bool | None = (
        None  # deploy key file exists (None: not an app with a repo)
    )
    env_present: tuple[bool, ...] = ()  # per declared env file: does it exist on the Pi
    requires_present: tuple[bool, ...] = ()  # per `requires` entry: satisfied on the Pi

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
