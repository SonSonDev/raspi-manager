"""Declare / forget an app's env files where the app's list really lives.

A Pi with its own `~/.raspi/apps.yaml` is the source of truth: the change is applied on
top of the file read from the Pi right now (so edits made since the last refresh are
kept), written atomically with a `.bak`, and read back. A Pi without that file keeps
using the PC's hosts.yaml, as before.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

import yaml

from raspi.config.loader import (
    ConfigError,
    check_env_path,
    check_label,
    load_hosts,
    parse_manifest,
)
from raspi.config.writer import add_env_file, remove_env_file
from raspi.core.manifest import render_manifest
from raspi.core.models import AppRef, EnvFile, Host
from raspi.core.remote import build_manifest_read_script, parse_env_read
from raspi.services.inventory import PublishPlan, publish
from raspi.ssh.transport import Transport


@dataclass
class DeclareResult:
    ok: bool
    message: str
    app: AppRef | None = None  # the app as it is now, when the change was applied


def _added(files: tuple[EnvFile, ...], label: str, path: str) -> tuple[EnvFile, ...]:
    label, path = check_label(label), check_env_path(path)
    if any(f.label == label for f in files):
        raise ConfigError(f"This app already has a file labelled '{label}'")
    if any(f.path == path for f in files):
        raise ConfigError("This path is already declared for this app")
    return files + (EnvFile(label, path),)


def _removed(files: tuple[EnvFile, ...], label: str) -> tuple[EnvFile, ...]:
    return tuple(f for f in files if f.label != label)


def _edit_on_pi(
    host: Host,
    app_name: str,
    edit: Callable[[tuple[EnvFile, ...]], tuple[EnvFile, ...]],
    transport: Transport,
) -> DeclareResult:
    read = transport.run_script(host, build_manifest_read_script())
    if not read.ok:
        return DeclareResult(False, f"Cannot read {host.name}: {read.error}")
    text = parse_env_read(read.stdout)
    if text is None:
        return DeclareResult(False, f"{host.name} has no apps.yaml any more.")
    try:
        apps = parse_manifest(yaml.safe_load(text))
        current = next((a for a in apps if a.name == app_name), None)
        if current is None:
            return DeclareResult(
                False, f"{app_name} is not in apps.yaml on {host.name}."
            )
        updated = replace(current, env_files=edit(current.env_files))
    except (yaml.YAMLError, ConfigError) as exc:
        return DeclareResult(False, str(exc))
    new_apps = tuple(updated if a.name == app_name else a for a in apps)
    plan = PublishPlan(True, "", render_manifest(new_apps))
    written = publish(replace(host, apps=new_apps), plan, transport)
    if not written.ok:
        return DeclareResult(False, written.message)
    return DeclareResult(
        True,
        f"Saved in ~/.raspi/apps.yaml on {host.name} (apps.yaml.bak keeps the previous one).",
        updated,
    )


def _edit_in_hosts_yaml(host: Host, app_name: str, change: Callable[[], None]):
    try:
        change()
        for configured in load_hosts():
            if configured.name == host.name:
                app = next((a for a in configured.apps if a.name == app_name), None)
                return DeclareResult(True, "Saved in ~/.raspi/hosts.yaml.", app)
    except ConfigError as exc:
        return DeclareResult(False, str(exc))
    return DeclareResult(False, f"{host.name} is not in hosts.yaml any more.")


def declare_env_file(
    host: Host,
    app_name: str,
    label: str,
    path: str,
    transport: Transport,
    on_pi: bool,
) -> DeclareResult:
    if on_pi:
        return _edit_on_pi(host, app_name, lambda f: _added(f, label, path), transport)
    return _edit_in_hosts_yaml(
        host, app_name, lambda: add_env_file(host.name, app_name, label, path)
    )


def forget_env_file(
    host: Host, app_name: str, label: str, transport: Transport, on_pi: bool
) -> DeclareResult:
    if on_pi:
        return _edit_on_pi(host, app_name, lambda f: _removed(f, label), transport)
    return _edit_in_hosts_yaml(
        host, app_name, lambda: remove_env_file(host.name, app_name, label)
    )
