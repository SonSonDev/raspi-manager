"""Install Node.js (through nvm) on a Pi."""

from __future__ import annotations

from raspi.core.models import Host
from raspi.core.runtime import (
    RuntimeError_,
    build_node_script,
    check_major,
    install_steps,
)
from raspi.services.provision import doctor
from raspi.ssh.transport import Transport

NVM_PROBE = '[ -s "$HOME/.nvm/nvm.sh" ] && echo yes || echo no\n'


def plan_node(host: Host, transport: Transport, major: str) -> tuple[list[str], str]:
    """Read-only: (steps, script). Raises RuntimeError_ when the Pi cannot do it."""
    major = check_major(major)
    report = doctor(host, transport)
    if not report.ok:
        raise RuntimeError_(report.message)
    keys = {c.key: c.ok for c in report.checks}
    for tool in ("curl", "git"):
        if not keys.get(tool):
            raise RuntimeError_(
                f"{tool} is missing on {host.name}: run provision first"
            )
    probe = transport.run_script(host, NVM_PROBE)
    have_nvm = probe.ok and probe.stdout.strip() == "yes"
    return install_steps(major, have_nvm), build_node_script(major, have_nvm)


def install_node(
    host: Host, transport: Transport, major: str, dry_run: bool = False
) -> tuple[bool, str, str]:
    """Returns (ok, message, script)."""
    try:
        _, script = plan_node(host, transport, major)
    except RuntimeError_ as exc:
        return False, str(exc), ""
    if dry_run:
        return True, "Dry run: nothing was changed.", script
    result = transport.run_script(host, script, timeout=900)
    if not result.ok:
        return False, f"Node install failed on {host.name}: {result.error}", script
    path = next(
        (ln[5:] for ln in result.stdout.splitlines() if ln.startswith("node=")), ""
    )
    where = f" ({path})" if path else ""
    return True, f"Node {major} installed on {host.name}{where}", script
