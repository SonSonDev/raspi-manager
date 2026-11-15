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

from raspi.core.models import AppRef

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


def render_unit(app: AppRef, user: str, workdir: str, node_bin: str) -> str:
    if not app.run:
        raise UnitError(f"{app.name} has no run command declared")
    path = f"{node_bin}:{BASE_PATH}" if node_bin else BASE_PATH
    lines = [
        MARKER,
        "[Unit]",
        f"Description={app.name} (raspi-manager)",
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
        f'ExecStart=/bin/sh -c "exec {systemd_quote(app.run)}"',
        "Restart=on-failure",
        "RestartSec=5",
        "",
        "[Install]",
        "WantedBy=multi-user.target",
    ]
    return "\n".join(lines) + "\n"


# --- what is on the Pi ----------------------------------------------------------------
def build_probe_script(app: AppRef) -> str:
    """Read-only: home folder, nvm's Node, sudo, the unit file (ours or foreign), env files."""
    unit = shlex.quote(f"{UNIT_DIR}/{app.unit}")
    lines = [
        'echo "home=$HOME"',
        'export NVM_DIR="$HOME/.nvm"; [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"',
        'n=$(command -v node || true); [ -z "$n" ] || echo "nodebin=$(dirname "$n")"',
        'echo "sudo=$(sudo -n true 2>/dev/null && echo yes || echo no)"',
        (
            f"if [ -e {unit} ]; then "
            f"if grep -qxF {shlex.quote(MARKER)} {unit} 2>/dev/null; then echo unit=ours; "
            f"else echo unit=foreign; fi; else echo unit=missing; fi"
        ),
    ]
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
    unit: str = "missing"  # missing | ours | foreign
    env_present: tuple[bool, ...] = ()


def parse_probe(output: str, env_count: int) -> UnitFacts:
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
        env_present=tuple(values.get(f"envfile{i}") == "yes" for i in range(env_count)),
    )


def build_install_script(app: AppRef, text: str) -> str:
    """Needs passwordless sudo. Enables the unit, does not start it."""
    unit = shlex.quote(app.unit)
    b64 = base64.b64encode(text.encode()).decode("ascii")
    return (
        "\n".join(
            [
                "set -e",
                'sudo -n true 2>/dev/null || { echo "passwordless sudo is required" >&2; exit 3; }',
                f"printf %s {b64} | base64 -d | sudo tee {UNIT_DIR}/{unit} >/dev/null",
                "sudo systemctl daemon-reload",
                f"sudo systemctl enable {unit}",
                f'echo "installed: {app.unit}"',
            ]
        )
        + "\n"
    )
