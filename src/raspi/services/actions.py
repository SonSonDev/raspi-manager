"""State-changing actions and log reading for one app on one host."""

from __future__ import annotations

from dataclasses import dataclass

from raspi.core.models import AppRef, Host
from raspi.core.remote import (
    build_action_script,
    build_logs_script,
    parse_action,
)
from raspi.ssh.transport import Transport


class LookupError_(Exception):
    """Unknown host or app name."""


@dataclass
class ActionResult:
    ok: bool
    message: str
    state: str = "unknown"
    script: str = ""  # what was (or, in dry-run, would be) executed
    executed: bool = True


def find_app(hosts: list[Host], host_name: str, app_name: str) -> tuple[Host, AppRef]:
    for host in hosts:
        if host.name == host_name:
            for app in host.apps:
                if app.name == app_name:
                    return host, app
            raise LookupError_(f"Unknown app '{app_name}' on host '{host_name}'")
    raise LookupError_(f"Unknown host '{host_name}'")


def run_action(
    host: Host,
    app: AppRef,
    action: str,
    transport: Transport,
    dry_run: bool = False,
) -> ActionResult:
    script = build_action_script(app.unit, action)
    if dry_run:
        return ActionResult(
            True, "Dry run: nothing was executed.", script=script, executed=False
        )
    result = transport.run_script(host, script)
    if not result.ok:
        return ActionResult(False, result.error, script=script)
    ok, message, state = parse_action(result.stdout)
    if not ok and "not found" in message.lower():
        message += (
            " -- the app has no systemd unit yet: create it with “Install unit…”."
        )
    if not ok and "password" in message.lower():
        message += " -- passwordless sudo is required for the Pi user."
    return ActionResult(
        ok, message or (f"{action} done" if ok else f"{action} failed"), state, script
    )


def read_logs(
    host: Host, app: AppRef, transport: Transport, lines: int = 200
) -> ActionResult:
    script = build_logs_script(app.unit, lines)
    result = transport.run_script(host, script, timeout=30.0)
    if not result.ok:
        return ActionResult(False, result.error, script=script)
    return ActionResult(True, result.stdout, script=script)
