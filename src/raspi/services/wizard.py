"""Live state of one app for the setup wizard (works before the app is in the inventory)."""

from __future__ import annotations

from raspi.core.models import AppRef, AppStatus, Host
from raspi.core.remote import build_status_script, parse_status
from raspi.ssh.transport import Transport


class StatusError(Exception):
    """The Pi could not be read."""


def read_status(host: Host, transport: Transport, app: AppRef) -> AppStatus:
    """Read-only: the same facts as the table's refresh, for this one app."""
    result = transport.run_script(host, build_status_script((app,)))
    if not result.ok:
        raise StatusError(f"Cannot read {host.name}: {result.error}")
    found = parse_status(result.stdout, (app,))
    if not found:
        raise StatusError(f"{host.name} returned nothing for {app.name}")
    return found[0]
