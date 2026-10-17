"""Build and parse the read-only status script run on a Pi. Pure functions."""

from __future__ import annotations

import re
import shlex

from raspi.core.models import AppRef, AppStatus

SHOW_PROPS = (
    "ActiveState",
    "SubState",
    "UnitFileState",
    "MainPID",
    "ActiveEnterTimestamp",
    "NRestarts",
)
_MARK = "@@APP "
_CREDENTIALS = re.compile(r"(?<=//)[^/@\s]+@")


def build_status_script(apps: tuple[AppRef, ...]) -> str:
    """Shell script that only reads state (systemctl show, git rev-parse/status/remote)."""
    lines = []
    for app in apps:
        lines.append(f"echo {shlex.quote(_MARK + app.name)}")
        props = " ".join(f"-p {p}" for p in SHOW_PROPS)
        lines.append(f"systemctl show {shlex.quote(app.unit)} {props} 2>&1")
        if app.path:
            p = shlex.quote(app.path)
            lines.append(f"if [ -d {p}/.git ]; then")
            lines.append(
                f'  echo "rev=$(git -C {p} rev-parse --short HEAD 2>/dev/null)"'
            )
            lines.append(
                f'  echo "dirty=$(git -C {p} status --porcelain 2>/dev/null | wc -l)"'
            )
            lines.append(
                f'  echo "remote=$(git -C {p} remote get-url origin 2>/dev/null)"'
            )
            lines.append("fi")
    return "\n".join(lines) + "\n"


ACTIONS = ("start", "stop", "restart")


def build_action_script(unit: str, action: str) -> str:
    """Script that changes one unit's state. Only the whitelisted actions are possible."""
    if action not in ACTIONS:
        raise ValueError(f"Unsupported action: {action!r}")
    u = shlex.quote(unit)
    return (
        f"sudo -n systemctl {action} {u} 2>&1\n"
        'echo "exit=$?"\n'
        f"systemctl show {u} -p ActiveState -p SubState 2>&1\n"
    )


def parse_action(output: str) -> tuple[bool, str, str]:
    """Return (ok, message, resulting ActiveState) from build_action_script output."""
    exit_code, state, message = None, "unknown", []
    for line in output.splitlines():
        line = line.rstrip("\r")
        if line.startswith("exit="):
            exit_code = line[5:]
        elif line.startswith("ActiveState="):
            state = line.split("=", 1)[1]
        elif line.startswith("SubState="):
            continue
        elif line.strip():
            message.append(line.strip())
    return exit_code == "0", " ".join(message), state


def build_logs_script(unit: str, lines: int) -> str:
    """Read-only: last `lines` journal entries of the unit."""
    n = max(1, min(int(lines), 1000))
    return f"journalctl -u {shlex.quote(unit)} -n {n} --no-pager -o short-iso 2>&1\n"


def parse_status(output: str, apps: tuple[AppRef, ...]) -> list[AppStatus]:
    by_name = {a.name: a for a in apps}
    result: list[AppStatus] = []
    current: AppStatus | None = None
    for raw in output.splitlines():
        line = raw.rstrip("\r")
        if line.startswith(_MARK):
            ref = by_name.get(line[len(_MARK) :])
            if ref is None:
                current = None
                continue
            current = AppStatus(name=ref.name, unit=ref.unit)
            result.append(current)
            continue
        if current is None or "=" not in line:
            continue
        key, _, value = line.partition("=")
        _apply(current, key, value.strip())
    return result


def _apply(app: AppStatus, key: str, value: str) -> None:
    if key == "ActiveState":
        app.active = value
    elif key == "SubState":
        app.sub = value
    elif key == "UnitFileState":
        app.enabled = value
    elif key == "MainPID":
        app.pid = int(value) if value.isdigit() else 0
    elif key == "ActiveEnterTimestamp":
        app.since = value
    elif key == "NRestarts":
        app.restarts = int(value) if value.isdigit() else 0
    elif key == "rev":
        app.rev = value
    elif key == "dirty":
        app.dirty = int(value) if value.isdigit() else None
    elif key == "remote":
        app.remote = _CREDENTIALS.sub("", value)  # never keep tokens embedded in URLs
