"""Installing a Cloudflare Tunnel connector on a Pi as its own systemd unit (pure; no I/O).

One unit per tunnel (`raspi-tunnel-<name>.service`), so a Pi can serve several tunnels,
unlike `cloudflared service install`, which owns a single `cloudflared.service`. The
tunnel token lives only in a root-only `EnvironmentFile` (`TUNNEL_TOKEN`), never in the
unit, so it is not visible in `systemctl status` or `ps`.
"""

from __future__ import annotations

import base64
import re
import shlex
from dataclasses import dataclass

TUNNEL_PREFIX = "raspi-manager-"
DEFAULT_BIN = "/usr/local/bin/cloudflared"
ENV_DIR = "/etc/cloudflared"
RELEASE_URL = "https://github.com/cloudflare/cloudflared/releases/download"
HIDDEN = "<hidden>"

# `uname -m` -> asset suffix of the official cloudflared release files
ASSETS = {"x86_64": "amd64", "aarch64": "arm64", "armv7l": "armhf", "armv6l": "arm"}
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ConnectorError(Exception):
    """The Pi cannot run the connector (or the install input is unusable)."""


def asset_for(machine: str) -> str:
    suffix = ASSETS.get(machine.strip())
    if suffix is None:
        raise ConnectorError(
            f"No official cloudflared build for architecture '{machine}'"
        )
    return f"cloudflared-linux-{suffix}"


def parse_release_checksum(notes: str, asset: str) -> str | None:
    """SHA-256 of `asset` from the release notes (`<file>: <64 hex>` lines)."""
    match = re.search(
        rf"^{re.escape(asset)}:\s*([0-9a-f]{{64}})\s*$", notes, re.MULTILINE
    )
    return match.group(1) if match else None


def unit_name_for(tunnel_name: str) -> str:
    base = tunnel_name.removeprefix(TUNNEL_PREFIX)
    if not _SAFE_NAME.match(base):
        raise ConnectorError(f"Unusable tunnel name: {tunnel_name!r}")
    return f"raspi-tunnel-{base}.service"


def env_path_for(unit: str) -> str:
    return f"{ENV_DIR}/{unit.removesuffix('.service')}.env"


def render_unit(tunnel_name: str, unit: str, bin_path: str) -> str:
    return (
        "[Unit]\n"
        f"Description=Cloudflare Tunnel {tunnel_name} (raspi-manager)\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"EnvironmentFile={env_path_for(unit)}\n"
        f"ExecStart={bin_path} --no-autoupdate tunnel run\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


# --- what is already on the Pi -----------------------------------------------------
def build_check_script(unit: str) -> str:
    """Read-only facts about the Pi: architecture, existing cloudflared, curl, sudo, unit."""
    u = shlex.quote(unit)
    return (
        'echo "machine=$(uname -m)"\n'
        'echo "bin=$(command -v cloudflared || true)"\n'
        "b=$(command -v cloudflared || true)\n"
        '[ -z "$b" ] || echo "version=$("$b" --version 2>&1 | head -1)"\n'
        'echo "curl=$(command -v curl >/dev/null && echo yes || echo no)"\n'
        'echo "sudo=$(sudo -n true 2>/dev/null && echo yes || echo no)"\n'
        f'echo "load=$(systemctl show {u} -p LoadState --value 2>/dev/null)"\n'
        f'echo "active=$(systemctl show {u} -p ActiveState --value 2>/dev/null)"\n'
    )


@dataclass(frozen=True)
class ConnectorState:
    machine: str = ""
    bin_path: str = ""  # "" when cloudflared is not installed
    version: str = ""
    has_curl: bool = False
    has_sudo: bool = False
    unit_loaded: bool = False
    unit_active: str = ""


def parse_check(output: str) -> ConnectorState:
    values: dict[str, str] = {}
    for raw in output.splitlines():
        key, sep, value = raw.rstrip("\r").partition("=")
        if sep:
            values[key] = value.strip()
    return ConnectorState(
        machine=values.get("machine", ""),
        bin_path=values.get("bin", ""),
        version=values.get("version", ""),
        has_curl=values.get("curl") == "yes",
        has_sudo=values.get("sudo") == "yes",
        unit_loaded=values.get("load") == "loaded",
        unit_active=values.get("active", ""),
    )


# --- installing ---------------------------------------------------------------------
@dataclass(frozen=True)
class Download:
    tag: str
    asset: str
    sha256: str

    @property
    def url(self) -> str:
        return f"{RELEASE_URL}/{self.tag}/{self.asset}"


def install_steps(unit: str, download: Download | None, bin_path: str) -> list[str]:
    """Human-readable list of what the install changes on the Pi (no secret in it)."""
    steps = []
    if download:
        steps.append(
            f"Download cloudflared {download.tag} ({download.asset}), check its SHA-256 "
            f"and install it as {DEFAULT_BIN}"
        )
    steps.append(
        f"Store the tunnel token in {env_path_for(unit)} (root only, mode 600)"
    )
    steps.append(
        f"Create the systemd unit /etc/systemd/system/{unit} (runs {bin_path})"
    )
    steps.append(f"Enable and start {unit}")
    return steps


def build_install_script(
    unit: str,
    unit_text: str,
    token: str,
    download: Download | None,
) -> str:
    """Needs passwordless sudo. `token` is a secret: pass HIDDEN to get a printable copy."""
    env_file = env_path_for(unit)
    lines = [
        "set -e",
        'sudo -n true 2>/dev/null || { echo "passwordless sudo is required" >&2; exit 3; }',
    ]
    if download:
        lines += [
            "tmp=$(mktemp -d); trap 'rm -rf \"$tmp\"' EXIT",
            f'curl -fsSL -o "$tmp/cloudflared" {shlex.quote(download.url)}',
            (
                f'echo {shlex.quote(download.sha256 + "  ")}"$tmp/cloudflared"'
                " | sha256sum -c - >/dev/null"
                ' || { echo "checksum mismatch: download refused" >&2; exit 4; }'
            ),
            f'sudo install -m 755 "$tmp/cloudflared" {DEFAULT_BIN}',
        ]
    unit_b64 = base64.b64encode(unit_text.encode()).decode("ascii")
    lines += [
        f"sudo install -d -m 755 {ENV_DIR}",
        f"( umask 077; printf 'TUNNEL_TOKEN=%s\\n' {shlex.quote(token)} | sudo tee {shlex.quote(env_file)} >/dev/null )",
        f"sudo chmod 600 {shlex.quote(env_file)}",
        f"printf %s {unit_b64} | base64 -d | sudo tee /etc/systemd/system/{shlex.quote(unit)} >/dev/null",
        "sudo systemctl daemon-reload",
        f"sudo systemctl enable --now {shlex.quote(unit)}",
        f'echo "installed: {unit}"',
    ]
    return "\n".join(lines) + "\n"


# --- finding the port an app listens on -------------------------------------------------
def build_ports_script(unit: str) -> str:
    """Read-only: listening TCP sockets of every process of the unit (its cgroup)."""
    u = shlex.quote(unit)
    return (
        f"cg=$(systemctl show {u} -p ControlGroup --value 2>/dev/null)\n"
        '[ -n "$cg" ] || exit 0\n'
        'for p in $(cat "/sys/fs/cgroup$cg/cgroup.procs" 2>/dev/null); do\n'
        '  ss -ltnpH 2>/dev/null | grep "pid=$p," || true\n'
        "done\n"
        "exit 0\n"  # a grep without match must not make the whole script fail
    )


def parse_ports(output: str) -> list[int]:
    """Distinct ports from `ss -ltnp` lines (`LISTEN 0 511 *:3000 *:* users:(...)`)."""
    ports = set()
    for line in output.splitlines():
        fields = line.split()
        if len(fields) >= 4 and fields[0] == "LISTEN":
            _, _, port = fields[3].rpartition(":")
            if port.isdigit() and 0 < int(port) < 65536:
                ports.add(int(port))
    return sorted(ports)
