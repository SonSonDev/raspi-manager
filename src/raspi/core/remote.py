"""Build and parse the read-only status script run on a Pi. Pure functions."""

from __future__ import annotations

import base64
import re
import shlex
from dataclasses import dataclass

from raspi.core.env import TEMPLATE_SUFFIXES, template_candidates
from raspi.core.models import AppRef, AppStatus

SHOW_PROPS = (
    "LoadState",
    "ActiveState",
    "SubState",
    "UnitFileState",
    "MainPID",
    "ActiveEnterTimestamp",
    "NRestarts",
)
_MARK = "@@APP "
_CREDENTIALS = re.compile(r"(?<=//)[^/@\s]+@")


def _workdir(app: AppRef) -> str | None:
    """Shell-quoted working copy of the app: its path, else ~/apps/<name> for a repo app."""
    if app.path:
        return shlex.quote(app.path)
    if app.repo:
        return f'"$HOME/apps/{app.name}"'
    return None


def _unit_read_lines(app: AppRef) -> list[str]:
    """Read-only: the fields of our own unit file that the inventory decides (unit drift)."""
    unit = shlex.quote(f"/etc/systemd/system/{app.unit}")
    marker = shlex.quote("# managed by raspi-manager")
    return [
        f"if grep -qxF {marker} {unit} 2>/dev/null; then",
        "  echo unitours=yes",
        '  echo "home=$HOME"',
        f"  sed -n 's/^ExecStart=/unitexec=/p' {unit} | head -n 1",
        f"  sed -n 's/^EnvironmentFile=/unitenv=/p' {unit}",
        f"  sed -n 's/^WorkingDirectory=/unitdir=/p' {unit} | head -n 1",
        "fi",
    ]


def build_status_script(apps: tuple[AppRef, ...]) -> str:
    """Shell script that only reads state (systemctl show, git rev-parse/status/remote)."""
    lines = []
    for app in apps:
        lines.append(f"echo {shlex.quote(_MARK + app.name)}")
        props = " ".join(f"-p {p}" for p in SHOW_PROPS)
        lines.append(f"systemctl show {shlex.quote(app.unit)} {props} 2>&1")
        lines += _unit_read_lines(app)
        workdir = _workdir(app)
        if app.repo:
            key = f'"$HOME/.ssh/raspi_deploy_{app.name}"'
            lines.append(f'echo "key=$([ -f {key} ] && echo yes || echo no)"')
        for i, req in enumerate(app.requires):
            p = shlex.quote(req.path)
            test = {
                "folder": f"[ -d {p} ]",
                "file": f"[ -f {p} ]",
                "mount": f"mountpoint -q {p}",
            }
            lines.append(f"{test[req.kind]} && echo req{i}=yes || echo req{i}=no")
        for i, route in enumerate(app.routes):
            url = shlex.quote(f"http://127.0.0.1:{route.port}{route.check}")
            # Status code only; the body is never read. 000 when nothing answers.
            lines.append(
                f"echo route{i}=$(curl -s -o /dev/null -m 3 -w '%{{http_code}}' {url} "
                "2>/dev/null || echo 000)"
            )
        for i, env in enumerate(app.env_files):
            p = shlex.quote(env.path)
            lines.append(f"[ -f {p} ] && echo envfile{i}=yes || echo envfile{i}=no")
        if workdir:
            p = workdir
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


_BACKUP_SUFFIXES = (".raspi-bak", ".bak")


@dataclass(frozen=True)
class EnvScan:
    folder: str  # absolute folder that was scanned
    files: list[str]  # real env files (not templates, not backups)
    templates: list[str]  # `.env.example` and friends


def build_env_detect_script(folder: str) -> str:
    """Read-only: list `.env*` files up to two levels below the app folder.

    `folder` may start with `$HOME/` (an app without a declared path): the answer then carries
    the absolute folder on its first line (`@@DIR=...`).
    """
    d = f'"{folder}"' if folder.startswith("$HOME/") else shlex.quote(folder)
    return (
        f'cd {d} 2>/dev/null || {{ echo "@@NODIR"; exit 0; }}\n'
        'echo "@@DIR=$PWD"\n'
        "find . -maxdepth 2 \\( -name node_modules -o -name .git -o -name dist \\) -prune "
        "-o -type f -name '.env*' -print 2>/dev/null\n"
    )


def parse_env_scan(folder: str, output: str) -> EnvScan | None:
    """Absolute env files and templates; None when the folder does not exist."""
    if output.strip() == "@@NODIR":
        return None
    base = folder.rstrip("/")
    files, templates = [], []
    for raw in output.splitlines():
        line = raw.strip()
        if line.startswith("@@DIR="):
            base = line[6:].rstrip("/")
            continue
        if not line.startswith("./") or line.endswith(_BACKUP_SUFFIXES):
            continue
        path = base + line[1:]
        (templates if line.endswith(TEMPLATE_SUFFIXES) else files).append(path)
    return EnvScan(base, sorted(files), sorted(templates))


def parse_env_detect(folder: str, output: str) -> list[str] | None:
    """Absolute paths of candidate env files (templates excluded); None if no such folder."""
    scan = parse_env_scan(folder, output)
    return None if scan is None else scan.files


def build_template_read_script(env_path: str) -> str:
    """Read-only: print the first template that exists next to `env_path`."""
    lines = []
    for candidate in template_candidates(env_path):
        q = shlex.quote(candidate)
        lines.append(
            f'if [ -f {q} ]; then echo "@@TEMPLATE {candidate}"; cat {q}; exit 0; fi'
        )
    return "\n".join(lines) + '\necho "@@NONE"\n'


def parse_template_read(output: str) -> tuple[str, str] | None:
    """(template path, content), or None when there is no template."""
    first, _, rest = output.partition("\n")
    if first.startswith("@@TEMPLATE "):
        return first[len("@@TEMPLATE ") :].strip(), rest
    return None


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
    if key == "LoadState":
        app.load = value
    elif key == "ActiveState":
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
    elif key == "key":
        app.key_present = value == "yes"
    elif key == "unitours":
        app.unit_ours = value == "yes"
    elif key == "unitexec":
        app.unit_exec = value
    elif key == "unitenv":
        app.unit_env = (*app.unit_env, value)
    elif key == "unitdir":
        app.unit_dir = value
    elif key == "home":
        app.home = value
    elif key.startswith("req") and key[3:].isdigit():
        index = int(key[3:])
        flags = list(app.requires_present) + [False] * (
            index + 1 - len(app.requires_present)
        )
        flags[index] = value == "yes"
        app.requires_present = tuple(flags)
    elif key.startswith("route") and key[5:].isdigit():
        index = int(key[5:])
        codes = list(app.route_codes) + [0] * (index + 1 - len(app.route_codes))
        codes[index] = int(value) if value.isdigit() else 0
        app.route_codes = tuple(codes)
    elif key.startswith("envfile") and key[7:].isdigit():
        index = int(key[7:])
        flags = list(app.env_present) + [False] * (index + 1 - len(app.env_present))
        flags[index] = value == "yes"
        app.env_present = tuple(flags)
    elif key == "rev":
        app.rev = value
    elif key == "dirty":
        app.dirty = int(value) if value.isdigit() else None
    elif key == "remote":
        app.remote = _CREDENTIALS.sub("", value)  # never keep tokens embedded in URLs
