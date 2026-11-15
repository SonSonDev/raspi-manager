"""What a Pi needs before it can run apps, and the script that installs the missing part (pure).

`build_check_script` only reads. `build_provision_script` changes the Pi and needs passwordless
sudo; it installs from the distribution's own repositories (no download of third-party scripts).
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass

APPS_DIR = "apps"  # relative to the login user's home
MIN_DISK_MB = 1024
APT_PACKAGES = ("git", "curl")  # the only packages provision may install


@dataclass(frozen=True)
class Check:
    key: str
    label: str
    ok: bool
    detail: str
    required: bool = True  # an optional item that is missing does not make the Pi unfit
    fix: str = ""  # what to do when it is missing
    installable: bool = False  # `provision` can fix it


def build_check_script() -> str:
    """Read-only facts about the Pi, as `key=value` lines."""
    return (
        'echo "os=$(. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME")"\n'
        'echo "machine=$(uname -m)"\n'
        'echo "sudo=$(sudo -n true 2>/dev/null && echo yes || echo no)"\n'
        'echo "git=$(git --version 2>/dev/null)"\n'
        'echo "curl=$(curl --version 2>/dev/null | head -1 | cut -d" " -f1,2)"\n'
        'echo "uv=$(uv --version 2>/dev/null || "$HOME/.local/bin/uv" --version 2>/dev/null)"\n'
        'n=$(command -v node || ls -d "$HOME"/.nvm/versions/node/*/bin/node 2>/dev/null | tail -1)\n'
        'echo "node=$([ -n "$n" ] && "$n" --version 2>/dev/null)"\n'
        'echo "cloudflared=$(cloudflared --version 2>/dev/null | head -1)"\n'
        f'echo "apps_dir=$([ -d "$HOME/{APPS_DIR}" ] && echo yes || echo no)"\n'
        'echo "disk_mb=$(df -Pm "$HOME" 2>/dev/null | awk \'NR==2{print $4}\')"\n'
        "echo \"mem_mb=$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo 2>/dev/null)\"\n"
    )


def _values(output: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in output.splitlines():
        key, sep, value = raw.rstrip("\r").partition("=")
        if sep:
            values[key] = value.strip()
    return values


def parse_checks(output: str) -> list[Check]:
    v = _values(output)
    disk = int(v["disk_mb"]) if v.get("disk_mb", "").isdigit() else None
    mem = v.get("mem_mb", "")
    checks = [
        Check(
            "os",
            "System",
            True,
            f"{v.get('os') or 'unknown'} ({v.get('machine', '?')})",
        ),
        Check(
            "sudo",
            "Passwordless sudo",
            v.get("sudo") == "yes",
            "yes" if v.get("sudo") == "yes" else "not available",
            fix="Allow the login user to run sudo without a password (sudo visudo).",
        ),
        Check(
            "git",
            "git",
            bool(v.get("git")),
            v.get("git") or "not installed",
            fix="apt install git",
            installable=True,
        ),
        Check(
            "curl",
            "curl",
            bool(v.get("curl")),
            v.get("curl") or "not installed",
            fix="apt install curl",
            installable=True,
        ),
        Check(
            "apps_dir",
            f"~/{APPS_DIR} folder",
            v.get("apps_dir") == "yes",
            "exists" if v.get("apps_dir") == "yes" else "missing",
            fix=f"mkdir ~/{APPS_DIR}",
            installable=True,
        ),
        Check(
            "disk",
            "Free disk space",
            disk is not None and disk >= MIN_DISK_MB,
            f"{disk} MB free" if disk is not None else "unknown",
            fix=f"Free at least {MIN_DISK_MB} MB.",
        ),
        Check(
            "node",
            "Node.js",
            bool(v.get("node")),
            v.get("node") or "not installed",
            required=False,
            fix="Needed by Node apps; not installed by raspi-manager yet.",
        ),
        Check(
            "uv",
            "uv (Python)",
            bool(v.get("uv")),
            v.get("uv") or "not installed",
            required=False,
            fix="Needed by Python apps; not installed by raspi-manager yet.",
        ),
        Check(
            "cloudflared",
            "cloudflared",
            bool(v.get("cloudflared")),
            v.get("cloudflared") or "not installed",
            required=False,
            fix="Installed by “Set up route…” when an app goes online.",
        ),
        Check("mem", "Memory", True, f"{mem} MB" if mem else "unknown", required=False),
    ]
    return checks


def missing_packages(checks: list[Check]) -> list[str]:
    absent = {c.key for c in checks if not c.ok}
    return [p for p in APT_PACKAGES if p in absent]


def provision_steps(checks: list[Check]) -> list[str]:
    """What `provision` would change, in plain words (empty: nothing to install)."""
    absent = {c.key for c in checks if not c.ok}
    steps = []
    packages = missing_packages(checks)
    if packages:
        steps.append(f"Install with apt: {', '.join(packages)}")
    if "apps_dir" in absent:
        steps.append(f"Create the folder ~/{APPS_DIR}")
    return steps


def build_provision_script(packages: list[str], make_apps_dir: bool) -> str:
    """Needs passwordless sudo. Only whitelisted package names are ever put in the command."""
    unknown = [p for p in packages if p not in APT_PACKAGES]
    if unknown:
        raise ValueError(f"Not an installable package: {', '.join(unknown)}")
    lines = ["set -e"]
    if packages:
        lines += [
            'sudo -n true 2>/dev/null || { echo "passwordless sudo is required" >&2; exit 3; }',
            "sudo apt-get update",
            "sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y "
            + " ".join(shlex.quote(p) for p in packages),
        ]
    if make_apps_dir:
        lines.append(f'mkdir -p "$HOME/{APPS_DIR}"')
    lines.append('echo "provisioned"')
    return "\n".join(lines) + "\n"
