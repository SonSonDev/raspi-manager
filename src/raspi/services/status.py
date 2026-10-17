"""Collect live state of every app on every host (never cached, see ARCHITECTURE.md)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from raspi.core.models import Host, HostStatus
from raspi.core.remote import build_status_script, parse_status
from raspi.ssh.transport import Transport


def collect_host(host: Host, transport: Transport) -> HostStatus:
    now = datetime.now()
    if not host.apps:
        return HostStatus(host, now)
    result = transport.run_script(host, build_status_script(host.apps))
    if not result.ok:
        return HostStatus(host, now, reachable=False, error=result.error)
    return HostStatus(host, now, apps=parse_status(result.stdout, host.apps))


def collect_all(hosts: list[Host], transport: Transport) -> list[HostStatus]:
    if not hosts:
        return []
    with ThreadPoolExecutor(max_workers=len(hosts)) as pool:
        return list(pool.map(lambda h: collect_host(h, transport), hosts))
