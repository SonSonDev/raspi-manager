"""systemd unit of a raspi-managed app (pure; no I/O).

The unit runs the app's `run` command as the login user, from its working copy, with the
environment files the app declares (`EnvironmentFile=`, the same files the Environment…
editor pushes) and the Node of nvm first in PATH. It is enabled but not started: the
environment and data folders may need attention before a first start.
"""

from __future__ import annotations

import base64
import shlex
from dataclasses import dataclass

from raspi.core.install import work_dir
from raspi.core.models import AppRef, AppStatus, Process, ProcessStatus
from raspi.core.processes import all_processes

MARKER = "# managed by raspi-manager"
UNIT_DIR = "/etc/systemd/system"
BASE_PATH = "/usr/local/bin:/usr/bin:/bin"


class UnitError(Exception):
    """The unit cannot be generated as declared."""


def systemd_quote(command: str) -> str:
    """Escape a command for `ExecStart=/bin/sh -c "..."` (specifiers and variables are systemd's)."""
    return (
        command.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("%", "%%")
        .replace("$", "$$")
    )


def render_unit(
    app: AppRef,
    user: str,
    workdir: str,
    node_bin: str,
    process: Process | None = None,
) -> str:
    """The unit of the app, or of one of its processes (same user, folder and env files)."""
    run = process.run if process else app.run
    label = f"{app.name} {process.name}" if process and app.processes else app.name
    if not run:
        raise UnitError(f"{label} has no run command declared")
    path = f"{node_bin}:{BASE_PATH}" if node_bin else BASE_PATH
    lines = [
        MARKER,
        "[Unit]",
        f"Description={label} (raspi-manager)",
        "After=network-online.target",
        "Wants=network-online.target",
        "",
        "[Service]",
        "Type=simple",
        f"User={user}",
        f"WorkingDirectory={workdir}",
        f"Environment=PATH={path}",
    ]
    lines += [f"EnvironmentFile={f.path}" for f in app.env_files]
    lines += [
        f'ExecStart=/bin/sh -c "exec {systemd_quote(run)}"',
        "Restart=on-failure",
        "RestartSec=5",
        "",
        "[Install]",
        "WantedBy=multi-user.target",
    ]
    return "\n".join(lines) + "\n"


def unit_drift(app: AppRef, status: AppStatus) -> list[str]:
    """What differs between our unit on the Pi and what the inventory implies (names, never values).

    Only a unit made by raspi-manager is compared (a foreign one is never ours to judge) and only
    for an app with a `run` command. Node's folder is left out: it changes with nvm, not with the
    inventory.
    """
    if app.processes:
        found = []
        for process, live in zip(app.processes, status.processes, strict=False):
            suffix = f" ({process.name})" if len(app.processes) > 1 else ""
            found += [f"{d}{suffix}" for d in _drift(app, process.run, live)]
        return found
    return _drift(app, app.run, status)


def _drift(
    app: AppRef, run: str | None, status: AppStatus | ProcessStatus
) -> list[str]:
    if not status.unit_ours or not run:
        return []
    drift = []
    if status.unit_exec != f'/bin/sh -c "exec {systemd_quote(run)}"':
        drift.append("run command")
    if status.unit_env != tuple(f.path for f in app.env_files):
        drift.append("environment files")
    if status.home:
        expected = work_dir(app).replace("$HOME", status.home)
        if status.unit_dir != expected:
            drift.append("working folder")
    return drift


# --- what is on the Pi ----------------------------------------------------------------
def build_probe_script(app: AppRef) -> str:
    """Read-only: home folder, nvm's Node, sudo, the unit files (ours or foreign), env files.

    `unit=` is the first process's unit; the others are `unit1=`, `unit2=`...
    """
    lines = [
        'echo "home=$HOME"',
        'export NVM_DIR="$HOME/.nvm"; [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"',
        'n=$(command -v node || true); [ -z "$n" ] || echo "nodebin=$(dirname "$n")"',
        'echo "sudo=$(sudo -n true 2>/dev/null && echo yes || echo no)"',
    ]
    for i, process in enumerate(all_processes(app)):
        unit = shlex.quote(f"{UNIT_DIR}/{process.unit}")
        key = "unit" if i == 0 else f"unit{i}"
        lines.append(
            f"if [ -e {unit} ]; then "
            f"if grep -qxF {shlex.quote(MARKER)} {unit} 2>/dev/null; then echo {key}=ours; "
            f"else echo {key}=foreign; fi; else echo {key}=missing; fi"
        )
    for i, f in enumerate(app.env_files):
        lines.append(
            f"[ -f {shlex.quote(f.path)} ] && echo envfile{i}=yes || echo envfile{i}=no"
        )
    return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class UnitFacts:
    home: str = ""
    node_bin: str = ""
    has_sudo: bool = False
    unit: str = "missing"  # missing | ours | foreign (the first process's)
    units: tuple[str, ...] = ()  # the same for every process, in order
    env_present: tuple[bool, ...] = ()


def parse_probe(output: str, env_count: int, process_count: int = 1) -> UnitFacts:
    values: dict[str, str] = {}
    for raw in output.splitlines():
        key, sep, value = raw.rstrip("\r").partition("=")
        if sep:
            values[key] = value.strip()
    return UnitFacts(
        home=values.get("home", ""),
        node_bin=values.get("nodebin", ""),
        has_sudo=values.get("sudo") == "yes",
        unit=values.get("unit", "missing"),
        units=tuple(
            values.get("unit" if i == 0 else f"unit{i}", "missing")
            for i in range(process_count)
        ),
        env_present=tuple(values.get(f"envfile{i}") == "yes" for i in range(env_count)),
    )


def build_install_script(app: AppRef, text: str) -> str:
    """Needs passwordless sudo. Enables the unit, does not start it."""
    return build_units_script(((app.unit, text),))


def build_units_script(units: tuple[tuple[str, str], ...]) -> str:
    """Write every (unit name, text), reload systemd once, enable each. Starts nothing."""
    lines = [
        "set -e",
        'sudo -n true 2>/dev/null || { echo "passwordless sudo is required" >&2; exit 3; }',
    ]
    for name, text in units:
        b64 = base64.b64encode(text.encode()).decode("ascii")
        path = f"{UNIT_DIR}/{shlex.quote(name)}"
        lines.append(f"printf %s {b64} | base64 -d | sudo tee {path} >/dev/null")
    lines.append("sudo systemctl daemon-reload")
    for name, _ in units:
        lines.append(f"sudo systemctl enable {shlex.quote(name)}")
        lines.append(f'echo "installed: {name}"')
    return "\n".join(lines) + "\n"
