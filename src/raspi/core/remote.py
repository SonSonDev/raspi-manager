"""Build and parse the read-only status script run on a Pi. Pure functions."""

from __future__ import annotations

import base64
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


ENV_MISSING = "@@MISSING"
ENV_EXISTS = "@@EXISTS"


def build_env_read_script(path: str) -> str:
    """Read-only: print the env file (first line says whether it exists)."""
    p = shlex.quote(path)
    return (
        f"if [ -f {p} ]; then echo {ENV_EXISTS}; cat {p}; else echo {ENV_MISSING}; fi\n"
    )


MANIFEST_PATH = ".raspi/apps.yaml"  # relative to the login user's home on the Pi


def build_manifest_read_script() -> str:
    """Read-only: print the Pi's app inventory (same marker protocol as the env read)."""
    return (
        f'f="$HOME/{MANIFEST_PATH}"\n'
        f'if [ -f "$f" ]; then echo {ENV_EXISTS}; cat "$f"; else echo {ENV_MISSING}; fi\n'
    )


def build_manifest_push_script(content: str) -> str:
    """Atomically write ~/.raspi/apps.yaml (mode 600), keeping one `.bak`. Content as base64."""
    b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
    return (
        "set -e\n"
        "umask 077\n"
        'dir="$HOME/.raspi"\n'
        'mkdir -p "$dir"\n'
        'target="$dir/apps.yaml"\n'
        'tmp=$(mktemp "$dir/.apps.XXXXXX")\n'
        f'printf %s {shlex.quote(b64)} | base64 -d > "$tmp"\n'
        '[ ! -e "$target" ] || cp -p "$target" "$target.bak"\n'
        'chmod 600 "$tmp"\n'
        'mv "$tmp" "$target"\n'
        'echo "written: $target"\n'
    )


def parse_env_read(output: str) -> str | None:
    """File content, or None when the file does not exist."""
    first, _, rest = output.partition("\n")
    return rest if first.strip() == ENV_EXISTS else None


_TEMPLATE_SUFFIXES = (".example", ".sample", ".template", ".dist", ".raspi-bak", ".bak")


def build_env_detect_script(folder: str) -> str:
    """Read-only: list `.env*` files up to two levels below the app folder."""
    d = shlex.quote(folder)
    return (
        f'cd {d} 2>/dev/null || {{ echo "@@NODIR"; exit 0; }}\n'
        "find . -maxdepth 2 \\( -name node_modules -o -name .git -o -name dist \\) -prune "
        "-o -type f -name '.env*' -print 2>/dev/null\n"
    )


def parse_env_detect(folder: str, output: str) -> list[str] | None:
    """Absolute paths of candidate env files; None when the folder does not exist."""
    if output.strip() == "@@NODIR":
        return None
    found = []
    for raw in output.splitlines():
        rel = raw.strip()
        if not rel.startswith("./") or rel.endswith(_TEMPLATE_SUFFIXES):
            continue
        found.append(folder.rstrip("/") + rel[1:])
    return sorted(found)


def build_env_push_script(path: str, content: str) -> str:
    """Atomically replace an env file (mode 600), keeping one backup. Content travels as base64."""
    p = shlex.quote(path)
    b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
    return (
        "set -e\n"
        "umask 077\n"
        f"target={p}\n"
        'dir=$(dirname "$target")\n'
        '[ -d "$dir" ] || { echo "No such directory: $dir" >&2; exit 3; }\n'
        'tmp=$(mktemp "$dir/.raspi-env.XXXXXX")\n'
        f'printf %s {shlex.quote(b64)} | base64 -d > "$tmp"\n'
        '[ ! -e "$target" ] || cp -p "$target" "$target.raspi-bak"\n'
        'chmod 600 "$tmp"\n'
        'mv "$tmp" "$target"\n'
        'echo "written: $(wc -l < "$target") lines"\n'
    )


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
