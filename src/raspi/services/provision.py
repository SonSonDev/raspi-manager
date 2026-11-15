"""Check a Pi (`doctor`, read-only) and install what it lacks (`provision`)."""

from __future__ import annotations

from dataclasses import dataclass, field

from raspi.core.models import Host
from raspi.core.provision import (
    Check,
    build_check_script,
    build_provision_script,
    missing_packages,
    parse_checks,
    provision_steps,
)
from raspi.ssh.transport import Transport


@dataclass
class DoctorReport:
    ok: bool  # the Pi answered
    message: str = ""
    checks: list[Check] = field(default_factory=list)

    @property
    def fit(self) -> bool:
        """Every required item is fine."""
        return self.ok and all(c.ok for c in self.checks if c.required)

    @property
    def steps(self) -> list[str]:
        return provision_steps(self.checks) if self.ok else []


def doctor(host: Host, transport: Transport) -> DoctorReport:
    result = transport.run_script(host, build_check_script())
    if not result.ok:
        return DoctorReport(False, f"Cannot reach {host.name}: {result.error}")
    return DoctorReport(True, checks=parse_checks(result.stdout))


@dataclass
class ProvisionResult:
    ok: bool
    message: str
    script: str = ""


def provision(
    host: Host, transport: Transport, dry_run: bool = False
) -> ProvisionResult:
    """Installs git/curl through apt and creates ~/apps, only what is missing right now."""
    report = doctor(host, transport)
    if not report.ok:
        return ProvisionResult(False, report.message)
    if not report.steps:
        return ProvisionResult(True, "Nothing to install.")
    if not next(c for c in report.checks if c.key == "sudo").ok and missing_packages(
        report.checks
    ):
        return ProvisionResult(
            False, f"{host.name} needs passwordless sudo to install packages."
        )
    script = build_provision_script(
        missing_packages(report.checks),
        any(c.key == "apps_dir" and not c.ok for c in report.checks),
    )
    if dry_run:
        return ProvisionResult(True, "Dry run: nothing was changed.", script)
    result = transport.run_script(host, script, timeout=600)
    if not result.ok:
        return ProvisionResult(
            False, f"Install failed on {host.name}: {result.error}", script
        )
    return ProvisionResult(True, f"{host.name} is provisioned.", script)
