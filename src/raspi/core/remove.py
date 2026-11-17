"""Removing an app: what can go, what is offered, the read-only probe and the script (pure; no I/O).

Every item is separate and ticked by the developer. Only the inventory entry and the unit are ticked
by default. Never touched, whatever is ticked: the folders of the app's `requires:` (its data),
anything outside the login user's home, and the Cloudflare route and tunnel (removed from the
Cloudflare window, where the connector and DNS are handled).
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field

from raspi.core.install import quoted_dir, work_dir
from raspi.core.models import AppRef
from raspi.core.unit import MARKER, UNIT_DIR

INVENTORY, UNIT, WORKDIR, KEY, ENV, LOCAL = (
    "inventory",
    "unit",
    "workdir",
    "key",
    "env",
    "local",
)
ORDER = (INVENTORY, UNIT, WORKDIR, KEY, ENV, LOCAL)
LABELS = {
    INVENTORY: "Entry in the Pi's app list (apps.yaml)",
    UNIT: "systemd unit (stopped, disabled, file deleted)",
    WORKDIR: "Working copy of the code",
    KEY: "Deploy key and its ssh alias",
    ENV: "Environment file(s) on the Pi",
    LOCAL: "Local copies of the environment on this PC",
}
DEFAULT_TICKED = (INVENTORY, UNIT)
KEY_MARKER = "# raspi-manager deploy key: "


@dataclass(frozen=True)
class Item:
    key: str
    label: str
    available: bool
    ticked: bool  # default state of the box
    note: str = ""  # why it is not available, or what exactly goes


@dataclass(frozen=True)
class RemoveFacts:
    home: str = ""
    has_sudo: bool = False
    unit: str = "missing"  # missing | ours | foreign
    workdir: str = "missing"  # missing | repo | other
    key_files: bool = False
    key_block: bool = False
    env_present: tuple[bool, ...] = ()


def key_path(name: str) -> str:
    return f"$HOME/.ssh/raspi_deploy_{name}"


def build_probe_script(app: AppRef) -> str:
    """Read-only facts about what exists on the Pi for this app."""
    unit = shlex.quote(f"{UNIT_DIR}/{app.unit}")
    d = quoted_dir(app)
    marker = shlex.quote(f"{KEY_MARKER}{app.name}")
    lines = [
        'echo "home=$HOME"',
        'echo "sudo=$(sudo -n true 2>/dev/null && echo yes || echo no)"',
        (
            f"if [ -e {unit} ]; then if grep -qxF {shlex.quote(MARKER)} {unit} 2>/dev/null; "
            "then echo unit=ours; else echo unit=foreign; fi; else echo unit=missing; fi"
        ),
        f"if [ -d {d}/.git ]; then echo workdir=repo; elif [ -e {d} ]; then echo workdir=other; else echo workdir=missing; fi",
        f'k="{key_path(app.name)}"',
        'if [ -e "$k" ] || [ -e "$k.pub" ]; then echo keyfiles=yes; else echo keyfiles=no; fi',
        f'if grep -qxF {marker} "$HOME/.ssh/config" 2>/dev/null; then echo keyblock=yes; else echo keyblock=no; fi',
    ]
    for i, env in enumerate(app.env_files):
        p = shlex.quote(env.path)
        lines.append(f"[ -f {p} ] && echo env{i}=yes || echo env{i}=no")
    return "\n".join(lines) + "\n"


def parse_probe(output: str, env_count: int) -> RemoveFacts:
    values: dict[str, str] = {}
    for raw in output.splitlines():
        key, sep, value = raw.rstrip("\r").partition("=")
        if sep:
            values[key] = value.strip()
    return RemoveFacts(
        home=values.get("home", ""),
        has_sudo=values.get("sudo") == "yes",
        unit=values.get("unit", "missing"),
        workdir=values.get("workdir", "missing"),
        key_files=values.get("keyfiles") == "yes",
        key_block=values.get("keyblock") == "yes",
        env_present=tuple(values.get(f"env{i}") == "yes" for i in range(env_count)),
    )


def _inside(path: str, folder: str) -> bool:
    return path == folder or path.startswith(folder.rstrip("/") + "/")


def _workdir_item(app: AppRef, facts: RemoveFacts) -> Item:
    absolute = work_dir(app).replace("$HOME", facts.home) if facts.home else ""
    label = LABELS[WORKDIR]
    if facts.workdir == "missing":
        return Item(WORKDIR, label, False, False, "there is none")
    if facts.workdir != "repo":
        return Item(WORKDIR, label, False, False, "not a git working copy: left alone")
    if not absolute.startswith(facts.home.rstrip("/") + "/"):
        return Item(WORKDIR, label, False, False, "outside the home folder: left alone")
    holding = [r.path for r in app.requires if _inside(r.path, absolute)]
    if holding:
        return Item(
            WORKDIR,
            label,
            False,
            False,
            f"it holds a prerequisite ({holding[0]}), so its data would go too: left alone",
        )
    return Item(WORKDIR, label, True, False, absolute)


def available_items(
    app: AppRef,
    facts: RemoveFacts,
    inventory_on_pi: bool,
    local_copies: bool,
) -> list[Item]:
    """The six items with their default state; unavailable ones say why."""
    items = [
        Item(
            INVENTORY,
            LABELS[INVENTORY],
            inventory_on_pi,
            inventory_on_pi,
            ""
            if inventory_on_pi
            else "declared in hosts.yaml on this PC: edit it there",
        )
    ]
    if facts.unit == "ours":
        unit_ok = facts.has_sudo
        note = f"{UNIT_DIR}/{app.unit}" if unit_ok else "needs passwordless sudo"
        items.append(Item(UNIT, LABELS[UNIT], unit_ok, unit_ok, note))
    else:
        why = (
            "not created by raspi-manager: left alone"
            if facts.unit == "foreign"
            else "there is none"
        )
        items.append(Item(UNIT, LABELS[UNIT], False, False, why))
    items.append(_workdir_item(app, facts))
    has_key = facts.key_files or facts.key_block
    items.append(
        Item(KEY, LABELS[KEY], has_key, False, "" if has_key else "there is none")
    )
    has_env = any(facts.env_present)
    items.append(
        Item(
            ENV,
            LABELS[ENV],
            has_env,
            False,
            ", ".join(e.path for e, p in zip(app.env_files, facts.env_present) if p)
            if has_env
            else "none declared or none on the Pi",
        )
    )
    items.append(
        Item(
            LOCAL,
            LABELS[LOCAL],
            local_copies,
            False,
            "" if local_copies else "there are none",
        )
    )
    return items


def key_removal_lines(name: str) -> list[str]:
    """Shell lines deleting one app's deploy key files and its alias block (shared with the key manager)."""
    alias = f"github-{name}"
    marker = f"{KEY_MARKER}{name}"
    return [
        f'k="{key_path(name)}"',
        'rm -f -- "$k" "$k.pub"',
        'c="$HOME/.ssh/config"',
        (
            f'if [ -f "$c" ] && grep -qxF {shlex.quote(marker)} "$c"; then '
            "tmp=$(mktemp); "
            f"awk -v m={shlex.quote(marker)} -v h={shlex.quote('Host ' + alias)} "
            "'$0==m { if ((getline nxt) > 0) { if (nxt==h) { for (i=0;i<4;i++) getline; next } "
            'else { print; print nxt; next } } } { print }\' "$c" > "$tmp" '
            '&& cat "$tmp" > "$c"; rm -f "$tmp"; fi'
        ),
    ]


def build_remove_script(app: AppRef, chosen: set[str], facts: RemoveFacts) -> str:
    """The commands on the Pi for the ticked items (inventory and local copies are not on the Pi)."""
    lines = ["set -e"]
    if UNIT in chosen:
        u = shlex.quote(app.unit)
        lines += [
            'sudo -n true 2>/dev/null || { echo "passwordless sudo is required" >&2; exit 3; }',
            f"sudo -n systemctl stop {u} || true",
            f"sudo -n systemctl disable {u} || true",
            f"sudo -n rm -f {shlex.quote(f'{UNIT_DIR}/{app.unit}')}",
            "sudo -n systemctl daemon-reload",
            f"sudo -n systemctl reset-failed {u} || true",
        ]
    if WORKDIR in chosen:
        d = quoted_dir(app)
        lines += [
            f"d={d}",
            'case "$d" in "$HOME"/?*) ;; *) echo "refusing: outside the home folder" >&2; exit 5;; esac',
            '[ -d "$d/.git" ] || { echo "refusing: not a git working copy" >&2; exit 6; }',
            'rm -rf -- "$d"',
        ]
    if KEY in chosen:
        lines += key_removal_lines(app.name)
    if ENV in chosen:
        for env, present in zip(app.env_files, facts.env_present):
            if present:
                p = shlex.quote(env.path)
                lines += [
                    f"p={p}",
                    'case "$p" in "$HOME"/?*) rm -f -- "$p" "$p.raspi-bak";; *) echo "skipped (outside home): $p" >&2;; esac',
                ]
    lines.append('echo "removed"')
    return "\n".join(lines) + "\n"


@dataclass
class RemoveSelection:
    items: set[str] = field(default_factory=set)

    @property
    def on_pi(self) -> bool:
        return bool(self.items & {UNIT, WORKDIR, KEY, ENV})
