"""Moving an app and its data to another Pi: probes, refusals, copy scripts (pure; no I/O).

The target must already have the app set up (inventory entry, working copy built at the same
revision, unit installed, not running): the New app wizard does that, minus its last step. Moving
then only carries what is not in git: the `requires: folder` entries (the data) and the env files.
Nothing here deletes anything on the source, and nothing here writes outside the declared folders.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass

from raspi.core.install import quoted_dir
from raspi.core.models import AppRef, Requirement

MAX_COPY_BYTES = (
    512 * 1024 * 1024
)  # the data is held in memory on this PC while it travels
MIN_SPARE_MB = 500
RUNNING = ("active", "activating", "reloading")


class MoveError(Exception):
    """The move cannot be planned or must not start."""


@dataclass(frozen=True)
class SideFacts:
    arch: str = ""
    node: str = ""
    state: str = (
        "missing"  # systemd ActiveState, or "missing" when the unit does not exist
    )
    rev: str = ""
    disk_mb: int = -1
    sudo: bool = False
    sizes: tuple[int, ...] = ()  # per data folder: bytes, -1 when absent
    folders: tuple[
        str, ...
    ] = ()  # per data folder: missing-parent | missing | empty | data
    mounts: tuple[bool, ...] = ()  # per `mount` prerequisite: present
    env_dirs: tuple[bool, ...] = ()  # per env file: its folder exists
    env_files: tuple[bool, ...] = ()  # per env file: the file exists


def data_folders(app: AppRef) -> tuple[Requirement, ...]:
    return tuple(r for r in app.requires if r.kind == "folder")


def mount_points(app: AppRef) -> tuple[Requirement, ...]:
    return tuple(r for r in app.requires if r.kind == "mount")


def build_probe_script(app: AppRef) -> str:
    """Read-only facts about one side (source or target) of the move."""
    d = quoted_dir(app)
    unit = shlex.quote(app.unit)
    lines = [
        'echo "arch=$(uname -m)"',
        'export NVM_DIR="$HOME/.nvm"',
        '[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh" >/dev/null 2>&1',
        'echo "node=$(node -v 2>/dev/null)"',
        'echo "sudo=$(sudo -n true 2>/dev/null && echo yes || echo no)"',
        'echo "disk_mb=$(df -Pm "$HOME" 2>/dev/null | awk \'NR==2{print $4}\')"',
        f'echo "rev=$(git -C {d} rev-parse HEAD 2>/dev/null)"',
        (
            f"if systemctl cat {unit} >/dev/null 2>&1; then "
            f'echo "state=$(systemctl show {unit} -p ActiveState --value)"; '
            "else echo state=missing; fi"
        ),
    ]
    for i, req in enumerate(data_folders(app)):
        p = shlex.quote(req.path)
        parent = shlex.quote(req.path.rsplit("/", 1)[0] or "/")
        lines += [
            f'echo "size{i}=$(du -sb {p} 2>/dev/null | cut -f1)"',
            (
                f"if [ -d {p} ]; then "
                f'if [ -n "$(ls -A {p} 2>/dev/null)" ]; then echo folder{i}=data; else echo folder{i}=empty; fi; '
                f"elif [ -d {parent} ]; then echo folder{i}=missing; else echo folder{i}=missing-parent; fi"
            ),
        ]
    for i, req in enumerate(mount_points(app)):
        lines.append(
            f"mountpoint -q {shlex.quote(req.path)} && echo mount{i}=yes || echo mount{i}=no"
        )
    for i, env in enumerate(app.env_files):
        p = shlex.quote(env.path)
        parent = shlex.quote(env.path.rsplit("/", 1)[0] or "/")
        lines += [
            f"[ -d {parent} ] && echo envdir{i}=yes || echo envdir{i}=no",
            f"[ -f {p} ] && echo env{i}=yes || echo env{i}=no",
        ]
    return "\n".join(lines) + "\n"


def parse_probe(output: str, app: AppRef) -> SideFacts:
    values: dict[str, str] = {}
    for raw in output.splitlines():
        key, sep, value = raw.rstrip("\r").partition("=")
        if sep:
            values[key] = value.strip()
    folders = data_folders(app)
    disk = values.get("disk_mb", "")
    return SideFacts(
        arch=values.get("arch", ""),
        node=values.get("node", ""),
        state=values.get("state", "missing") or "missing",
        rev=values.get("rev", ""),
        disk_mb=int(disk) if disk.isdigit() else -1,
        sudo=values.get("sudo") == "yes",
        sizes=tuple(
            int(values[f"size{i}"]) if values.get(f"size{i}", "").isdigit() else -1
            for i in range(len(folders))
        ),
        folders=tuple(values.get(f"folder{i}", "missing") for i in range(len(folders))),
        mounts=tuple(
            values.get(f"mount{i}") == "yes" for i in range(len(mount_points(app)))
        ),
        env_dirs=tuple(
            values.get(f"envdir{i}") == "yes" for i in range(len(app.env_files))
        ),
        env_files=tuple(
            values.get(f"env{i}") == "yes" for i in range(len(app.env_files))
        ),
    )


def node_major(version: str) -> str:
    return version.lstrip("v").split(".", 1)[0]


def total_bytes(source: SideFacts) -> int:
    return sum(max(s, 0) for s in source.sizes)


def check_move(
    app: AppRef, source: SideFacts, target: SideFacts, target_name: str
) -> tuple[list[str], list[str]]:
    """(reasons to refuse, warnings). The move starts only when there is no reason to refuse."""
    refuse: list[str] = []
    warn: list[str] = []
    if target.state == "missing":
        refuse.append(
            f"{app.name} has no systemd unit on {target_name}: set it up there first "
            "(App → New app…, up to the unit; do not start it)"
        )
    elif target.state in RUNNING:
        refuse.append(f"{app.name} is already running on {target_name}: stop it first")
    if not target.rev:
        refuse.append(
            f"{app.name} is not cloned on {target_name}: set it up there first"
        )
    elif source.rev and target.rev != source.rev:
        refuse.append(
            f"{target_name} is at {target.rev[:7]}, the source at {source.rev[:7]}: "
            "update the target to the same revision first"
        )
    if source.arch and target.arch and source.arch != target.arch:
        refuse.append(f"CPU architecture differs ({source.arch} → {target.arch})")
    if source.node and node_major(source.node) != node_major(target.node):
        refuse.append(
            f"Node differs ({source.node or 'none'} → {target.node or 'none'}): "
            "install the same major version on the target"
        )
    if not target.sudo:
        refuse.append(f"{target_name} needs passwordless sudo to start the unit")
    for req, ok in zip(mount_points(app), target.mounts):
        if not ok:
            refuse.append(f"{req.path} is not mounted on {target_name}")
    for req, state in zip(data_folders(app), target.folders):
        if state == "data":
            refuse.append(
                f"{req.path} on {target_name} already holds files: never overwritten"
            )
        elif state == "missing-parent":
            refuse.append(
                f"the parent of {req.path} does not exist on {target_name} (mount or create it)"
            )
    for env, has_dir in zip(app.env_files, target.env_dirs):
        if not has_dir:
            refuse.append(f"the folder of {env.path} does not exist on {target_name}")
    for env, there in zip(app.env_files, target.env_files):
        if there:
            warn.append(
                f"{env.path} on {target_name} is replaced (previous copy kept as .raspi-bak)"
            )
    size = total_bytes(source)
    if size > MAX_COPY_BYTES:
        refuse.append(
            f"{size // (1024 * 1024)} MB of data exceeds the {MAX_COPY_BYTES // (1024 * 1024)} MB "
            "this window carries; copy it by hand with the app stopped"
        )
    need_mb = size // (1024 * 1024) * 2 + MIN_SPARE_MB
    if 0 <= target.disk_mb < need_mb:
        refuse.append(
            f"only {target.disk_mb} MB free on {target_name}, {need_mb} MB needed"
        )
    if any(s < 0 for s in source.sizes):
        warn.append("a data folder is missing on the source: nothing to copy for it")
    if not data_folders(app) and not app.env_files:
        warn.append(
            "the app declares no data folder and no env file: only the start moves"
        )
    return refuse, warn


def move_steps(app: AppRef, source: str, target: str, was_running: bool) -> list[str]:
    steps = []
    if was_running:
        steps.append(
            f"Stop {app.unit} on {source} (clean SIGTERM) and wait until it is stopped"
        )
    for req in data_folders(app):
        steps.append(f"Copy {req.path} from {source} to {target} (tar through this PC)")
    for env in app.env_files:
        steps.append(f"Copy the env file {env.path} (values are never shown)")
    if data_folders(app):
        steps.append(
            "Compare SHA-256 of every copied file on both sides; abort on any difference"
        )
    if was_running:
        steps.append(f"Start {app.unit} on {target} and check it is active")
        steps.append(f"Disable {app.unit} on {source}; its data stays where it is")
    else:
        steps.append(
            f"{app.name} was not running on {source}: nothing is started on {target}"
        )
    steps.append(
        f"On any failure before the start: restart {source} and clean what was copied"
    )
    return steps


def build_pack_script(path: str) -> str:
    return f"tar -C {shlex.quote(path)} -cf - .\n"


def build_unpack_script(path: str) -> str:
    p = shlex.quote(path)
    return f"set -e\n[ -d {p} ] || mkdir {p}\ntar -C {p} -xf -\n"


def build_manifest_script(path: str) -> str:
    """Relative path and SHA-256 of every file, sorted: equal text means equal content."""
    p = shlex.quote(path)
    return (
        f"cd {p} 2>/dev/null || exit 0\n"
        "LC_ALL=C find . -type f -exec sha256sum {} + | LC_ALL=C sort -k2\n"
    )


def build_cleanup_script(path: str) -> str:
    """Empties a target folder that was checked empty before the copy (never the folder itself)."""
    return f"find {shlex.quote(path)} -mindepth 1 -delete\n"


def build_disable_script(unit: str) -> str:
    return f"sudo -n systemctl disable {shlex.quote(unit)} 2>&1\n"
