"""Create and enable the systemd unit of an app declared with a `run` command."""

from __future__ import annotations

from dataclasses import dataclass, field

from raspi.core.install import work_dir
from raspi.core.models import AppRef, Host
from raspi.core.unit import (
    UNIT_DIR,
    UnitError,
    build_install_script,
    build_probe_script,
    parse_probe,
    render_unit,
)
from raspi.ssh.transport import Transport


@dataclass
class UnitPlan:
    app: AppRef
    text: str
    script: str
    steps: list[str]
    warnings: list[str] = field(default_factory=list)


def plan_unit(host: Host, transport: Transport, app: AppRef) -> UnitPlan:
    """Read-only. Raises UnitError when the unit cannot be created as declared."""
    if not app.run:
        raise UnitError(f"{app.name} has no run command declared")
    probe = transport.run_script(host, build_probe_script(app))
    if not probe.ok:
        raise UnitError(f"Cannot inspect {host.name}: {probe.error}")
    facts = parse_probe(probe.stdout, len(app.env_files))
    if not facts.has_sudo:
        raise UnitError(f"{host.name} needs passwordless sudo to install a unit")
    if facts.unit == "foreign":
        raise UnitError(
            f"{UNIT_DIR}/{app.unit} exists and was not made by raspi-manager: "
            "not overwritten"
        )
    workdir = work_dir(app).replace("$HOME", facts.home) if facts.home else ""
    if not workdir.startswith("/"):
        raise UnitError(f"Cannot work out the folder of {app.name} on {host.name}")
    text = render_unit(app, host.user, workdir, facts.node_bin)
    warnings = []
    if not facts.node_bin:
        warnings.append("No Node found on the Pi: the app may not start")
    if not app.env_files:
        warnings.append("No environment file declared: the unit gets no variables")
    for env, present in zip(app.env_files, facts.env_present, strict=True):
        if not present:
            warnings.append(f"{env.path} does not exist yet: push it before starting")
    verb = "Replace" if facts.unit == "ours" else "Create"
    steps = [
        f"{verb} {UNIT_DIR}/{app.unit} (runs `{app.run}` as {host.user} in {workdir})",
        "Reload systemd and enable the unit (it is not started)",
    ]
    return UnitPlan(app, text, build_install_script(app, text), steps, warnings)


@dataclass
class UnitResult:
    ok: bool
    message: str


def install_unit(host: Host, transport: Transport, plan: UnitPlan) -> UnitResult:
    result = transport.run_script(host, plan.script, timeout=60)
    if not result.ok:
        return UnitResult(False, f"Unit install failed on {host.name}: {result.error}")
    return UnitResult(True, f"{plan.app.unit} is enabled on {host.name} (not started).")
