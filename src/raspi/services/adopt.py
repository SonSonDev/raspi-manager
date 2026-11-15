"""Adopt apps a Pi already runs: read-only scan, then add the chosen ones to its inventory."""

from __future__ import annotations

from dataclasses import dataclass, replace

import yaml

from raspi.config.loader import ConfigError, parse_manifest
from raspi.core.manifest import render_manifest
from raspi.core.models import AppRef, Host
from raspi.core.remote import build_manifest_read_script, parse_env_read
from raspi.core.scan import Candidate, build_scan_script, parse_scan
from raspi.services.declare import DeclareResult
from raspi.services.inventory import PublishPlan, publish
from raspi.ssh.transport import Transport


@dataclass
class ScanResult:
    ok: bool
    candidates: list[Candidate]
    message: str = ""


def candidate_to_app(candidate: Candidate) -> AppRef | None:
    """The app we would declare for it, or None when its names are not acceptable."""
    entry: dict = {"name": candidate.name, "unit": candidate.unit}
    if candidate.repo:
        entry["path"] = candidate.repo
    try:
        return parse_manifest({"version": 1, "apps": [entry]})[0]
    except ConfigError:
        return None


def scan_host(host: Host, transport: Transport) -> ScanResult:
    """Read-only. Units run by the login user and not yet in the inventory."""
    result = transport.run_script(host, build_scan_script())
    if not result.ok:
        return ScanResult(False, [], f"Cannot scan {host.name}: {result.error}")
    known = {a.unit for a in host.apps}
    found = [
        c
        for c in parse_scan(result.stdout)
        if c.user == host.user and c.unit not in known and candidate_to_app(c)
    ]
    return ScanResult(True, found)


def add_apps(
    host: Host, new: tuple[AppRef, ...], transport: Transport, dry_run: bool = False
) -> DeclareResult:
    """Append apps to the Pi's apps.yaml (created from `host.apps` when it has none yet).

    Applied on the file read right now, atomic with a `.bak`, read back afterwards.
    """
    read = transport.run_script(host, build_manifest_read_script())
    if not read.ok:
        return DeclareResult(False, f"Cannot read {host.name}: {read.error}")
    text = parse_env_read(read.stdout)
    try:
        base = host.apps if text is None else parse_manifest(yaml.safe_load(text))
        taken_names = {a.name for a in base}
        taken_units = {a.unit for a in base}
        for app in new:
            if app.name in taken_names or app.unit in taken_units:
                raise ConfigError(
                    f"{app.name} ({app.unit}) is already in the inventory"
                )
        apps = base + new
        content = render_manifest(apps)
        parse_manifest(yaml.safe_load(content))
    except (yaml.YAMLError, ConfigError) as exc:
        return DeclareResult(False, str(exc))
    written = publish(
        replace(host, apps=apps), PublishPlan(True, "", content), transport, dry_run
    )
    if not written.ok:
        return DeclareResult(False, written.message)
    names = ", ".join(a.name for a in new)
    if dry_run:
        return DeclareResult(True, f"Dry run: would add {names}; nothing was written.")
    return DeclareResult(True, f"Added {names} to ~/.raspi/apps.yaml on {host.name}.")
