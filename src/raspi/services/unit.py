"""Create and enable the systemd unit of an app declared with a `run` command."""

from __future__ import annotations

from dataclasses import dataclass, field

from raspi.core.install import work_dir
from raspi.core.models import AppRef, Host
from raspi.core.processes import all_processes
from raspi.core.unit import (
    UNIT_DIR,
    UnitError,
    build_probe_script,
    build_units_script,
    parse_probe,
    render_unit,
)
from raspi.ssh.transport import Transport


@dataclass
class UnitPlan:
    app: AppRef
    text: str  # every unit file, one after the other when the app has several processes
    script: str
    steps: list[str]
    warnings: list[str] = field(default_factory=list)
    existing: bool = False  # replaces a unit of ours (False: creates it)


def plan_unit(host: Host, transport: Transport, app: AppRef) -> UnitPlan:
    """Read-only. Raises UnitError when the unit cannot be created as declared."""
    processes = all_processes(app)
    for process in processes:
        if not process.run:
            raise UnitError(f"{process.name} of {app.name} has no run command declared")
    probe = transport.run_script(host, build_probe_script(app))
    if not probe.ok:
        raise UnitError(f"Cannot inspect {host.name}: {probe.error}")
    facts = parse_probe(probe.stdout, len(app.env_files), len(processes))
    if not facts.has_sudo:
        raise UnitError(f"{host.name} needs passwordless sudo to install a unit")
    for process, state in zip(processes, facts.units, strict=True):
        if state == "foreign":
            raise UnitError(
                f"{UNIT_DIR}/{process.unit} exists and was not made by raspi-manager: "
                "not overwritten"
            )
    workdir = work_dir(app).replace("$HOME", facts.home) if facts.home else ""
    if not workdir.startswith("/"):
        raise UnitError(f"Cannot work out the folder of {app.name} on {host.name}")
    rendered = tuple(
        (p.unit, render_unit(app, host.user, workdir, facts.node_bin, p))
        for p in processes
    )
    text = (
        rendered[0][1]
        if len(rendered) == 1
        else "\n".join(f"# ===== {name} =====\n{body}" for name, body in rendered)
    )
    warnings = []
    if not facts.node_bin:
        warnings.append("No Node found on the Pi: the app may not start")
    if not app.env_files:
        warnings.append("No environment file declared: the unit gets no variables")
    for env, present in zip(app.env_files, facts.env_present, strict=True):
        if not present:
            warnings.append(f"{env.path} does not exist yet: push it before starting")
    steps = [
        f"{'Replace' if state == 'ours' else 'Create'} {UNIT_DIR}/{p.unit} "
        f"(runs `{p.run}` as {host.user} in {workdir})"
        for p, state in zip(processes, facts.units, strict=True)
    ]
    steps.append(
        "Reload systemd and enable the unit (it is not started)"
        if len(processes) == 1
        else "Reload systemd and enable the units (none is started or restarted)"
    )
    return UnitPlan(
        app,
        text,
        build_units_script(rendered),
        steps,
        warnings,
        existing="ours" in facts.units,
    )


@dataclass
class UnitResult:
    ok: bool
    message: str


def install_unit(host: Host, transport: Transport, plan: UnitPlan) -> UnitResult:
    result = transport.run_script(host, plan.script, timeout=60)
    if not result.ok:
        return UnitResult(False, f"Unit install failed on {host.name}: {result.error}")
    return UnitResult(True, f"{plan.app.unit} is enabled on {host.name} (not started).")
