"""Which apps a Pi has: its own ~/.raspi/apps.yaml wins, hosts.yaml is the fallback."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace

import yaml

from raspi.config.loader import ConfigError, parse_manifest
from raspi.core.manifest import render_manifest
from raspi.core.models import Host
from raspi.core.remote import (
    MANIFEST_PATH,
    build_manifest_push_script,
    build_manifest_read_script,
    parse_env_read,
)
from raspi.ssh.transport import RunResult, Transport


@dataclass
class Inventory:
    host: Host  # apps = the Pi's list when it has a valid apps.yaml, else the PC's
    source: str  # "pi" or "pc"
    error: str = ""  # apps.yaml found but unusable (the PC list is kept)
    read: RunResult | None = None  # the SSH result, so callers can report unreachable


def read_inventory(host: Host, transport: Transport) -> Inventory:
    result = transport.run_script(host, build_manifest_read_script())
    if not result.ok:
        return Inventory(host, "pc", read=result)
    text = parse_env_read(result.stdout)
    if text is None:
        return Inventory(host, "pc", read=result)
    try:
        apps = parse_manifest(yaml.safe_load(text))
    except (yaml.YAMLError, ConfigError) as exc:
        message = f"~/.raspi/apps.yaml on {host.name} is ignored: {exc}"
        return Inventory(host, "pc", message, result)
    return Inventory(replace(host, apps=apps), "pi", read=result)


def resolve_all(hosts: list[Host], transport: Transport) -> list[Host]:
    """Every host with the apps it really has (reads one small file per Pi, in parallel)."""
    if not hosts:
        return []
    with ThreadPoolExecutor(max_workers=len(hosts)) as pool:
        return list(pool.map(lambda h: read_inventory(h, transport).host, hosts))


@dataclass
class PublishPlan:
    ok: bool  # False: nothing to publish (see `message`)
    message: str
    content: str = ""  # exact apps.yaml text that would be written
    path: str = f"~/{MANIFEST_PATH}"


@dataclass
class PublishResult:
    ok: bool
    message: str
    executed: bool = True


def plan_publish(host: Host, transport: Transport) -> PublishPlan:
    """Read-only: what publishing the hosts.yaml apps of `host` to its Pi would write.

    Only a Pi without apps.yaml is accepted: replacing an existing inventory would let
    the PC overwrite the source of truth, so that goes through explicit edits instead.
    """
    if not host.apps:
        return PublishPlan(False, f"hosts.yaml declares no app for {host.name}.")
    content = render_manifest(host.apps)
    if parse_manifest(yaml.safe_load(content)) != host.apps:
        raise ConfigError("Internal error: the rendered inventory does not round-trip.")
    read = transport.run_script(host, build_manifest_read_script())
    if not read.ok:
        return PublishPlan(False, f"Cannot read {host.name}: {read.error}")
    existing = parse_env_read(read.stdout)
    if existing is None:
        return PublishPlan(True, f"{host.name} has no apps.yaml yet.", content)
    if existing == content:
        return PublishPlan(False, f"{host.name} already has exactly this inventory.")
    return PublishPlan(
        False,
        f"{host.name} already has an apps.yaml, which is the source of truth: "
        "it is not replaced from the PC.",
    )


def publish(
    host: Host, plan: PublishPlan, transport: Transport, dry_run: bool = False
) -> PublishResult:
    """Write the planned apps.yaml to the Pi, then read it back to check it."""
    if not plan.ok:
        return PublishResult(False, plan.message, executed=False)
    if dry_run:
        return PublishResult(True, "Dry run: nothing was written.", executed=False)
    result = transport.run_script(host, build_manifest_push_script(plan.content))
    if not result.ok:
        return PublishResult(False, result.error)
    back = transport.run_script(host, build_manifest_read_script())
    if not back.ok or parse_env_read(back.stdout) != plan.content:
        return PublishResult(False, "Written, but reading it back did not match.")
    return PublishResult(True, f"Published {len(host.apps)} app(s) to {plan.path}.")
