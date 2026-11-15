"""Collect live state of every app on every host (never cached, see ARCHITECTURE.md)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from raspi.core.models import Host, HostStatus
from raspi.core.remote import build_status_script, parse_status
from raspi.services.inventory import read_inventory
from raspi.ssh.transport import Transport


def collect_host(host: Host, transport: Transport) -> HostStatus:
    now = datetime.now()
    inv = read_inventory(host, transport)
    if inv.read is not None and not inv.read.ok:
        return HostStatus(host, now, reachable=False, error=inv.read.error)
    host = inv.host
    note = {"inventory": inv.source, "inventory_error": inv.error}
    if not host.apps:
        return HostStatus(host, now, **note)
    result = transport.run_script(host, build_status_script(host.apps))
    if not result.ok:
        return HostStatus(host, now, reachable=False, error=result.error, **note)
    return HostStatus(host, now, apps=parse_status(result.stdout, host.apps), **note)


def collect_all(hosts: list[Host], transport: Transport) -> list[HostStatus]:
    if not hosts:
        return []
    with ThreadPoolExecutor(max_workers=len(hosts)) as pool:
        return list(pool.map(lambda h: collect_host(h, transport), hosts))
