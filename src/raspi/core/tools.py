"""Extra tools some apps need on a Pi: ffmpeg (apt) and yt-dlp (official release) (pure; no I/O).

ffmpeg comes from the distribution (signed by Debian). yt-dlp comes from the official GitHub
release, not from apt: Debian's package is months old and yt-dlp stops working when it lags
behind the sites it reads. The download is checked against the SHA-256 published with the
release and must RUN on the Pi before anything is installed over the current version.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

FFMPEG, YTDLP = "ffmpeg", "yt-dlp"
TOOLS = (FFMPEG, YTDLP)
RELEASE_URL = "https://github.com/yt-dlp/yt-dlp/releases/download"
BIN = "/usr/local/bin/yt-dlp"
BUNDLE_DIR = "/opt/yt-dlp"
# `uname -m` -> (release file, shape): a single executable, or a zipped folder (PyInstaller onedir)
ASSETS = {
    "x86_64": ("yt-dlp_linux", "file"),
    "aarch64": ("yt-dlp_linux_aarch64", "file"),
    "armv7l": ("yt-dlp_linux_armv7l.zip", "bundle"),
}
_TAG = re.compile(r"^\d{4}\.\d{2}\.\d{2}(\.\d+)?$")


class ToolError(Exception):
    """A tool cannot be installed as asked."""


@dataclass(frozen=True)
class YtDlpDownload:
    tag: str
    asset: str
    shape: str  # file | bundle
    sha256: str

    @property
    def url(self) -> str:
        return f"{RELEASE_URL}/{self.tag}/{self.asset}"

    @property
    def launcher(self) -> str:
        """Executable inside a bundle (the zip's name without .zip)."""
        return self.asset.removesuffix(".zip")


def asset_for(machine: str) -> tuple[str, str]:
    found = ASSETS.get(machine.strip())
    if found is None:
        raise ToolError(f"No official yt-dlp build for architecture '{machine}'")
    return found


def check_tag(tag: str) -> str:
    if not _TAG.match(tag):
        raise ToolError(f"Unexpected yt-dlp release tag: {tag!r}")
    return tag


def parse_sums(text: str, asset: str) -> str | None:
    """SHA-256 of `asset` from the release's SHA2-256SUMS (`<64 hex>  <file>` lines)."""
    match = re.search(
        rf"^([0-9a-f]{{64}})\s+\*?{re.escape(asset)}\s*$", text, re.MULTILINE
    )
    return match.group(1) if match else None


# --- what is on the Pi ----------------------------------------------------------------
def build_probe_script() -> str:
    """Read-only."""
    return (
        'echo "machine=$(uname -m)"\n'
        'echo "sudo=$(sudo -n true 2>/dev/null && echo yes || echo no)"\n'
        'echo "curl=$(command -v curl >/dev/null && echo yes || echo no)"\n'
        'echo "unzip=$(command -v unzip >/dev/null && echo yes || echo no)"\n'
        'echo "ffmpeg=$(ffmpeg -version 2>/dev/null | head -1)"\n'
        'echo "ytdlp=$(yt-dlp --version 2>/dev/null)"\n'
    )


@dataclass(frozen=True)
class ToolFacts:
    machine: str = ""
    has_sudo: bool = False
    has_curl: bool = False
    has_unzip: bool = False
    ffmpeg: str = ""  # first line of `ffmpeg -version`, "" when absent
    ytdlp: str = ""  # `yt-dlp --version`, "" when absent


def parse_probe(output: str) -> ToolFacts:
    values: dict[str, str] = {}
    for raw in output.splitlines():
        key, sep, value = raw.rstrip("\r").partition("=")
        if sep:
            values[key] = value.strip()
    return ToolFacts(
        machine=values.get("machine", ""),
        has_sudo=values.get("sudo") == "yes",
        has_curl=values.get("curl") == "yes",
        has_unzip=values.get("unzip") == "yes",
        ffmpeg=values.get("ffmpeg", ""),
        ytdlp=values.get("ytdlp", ""),
    )


def ffmpeg_short(line: str) -> str:
    """`ffmpeg version 7.1.2-0+deb13u1 Copyright ...` -> `7.1.2-0+deb13u1`."""
    parts = line.split()
    return parts[2] if len(parts) > 2 and parts[1] == "version" else line


# --- the plan --------------------------------------------------------------------------
def needs_ytdlp(facts: ToolFacts, latest_tag: str) -> bool:
    return facts.ytdlp != latest_tag


def tools_steps(
    want_ffmpeg: bool,
    download: YtDlpDownload | None,
    apt_extra: list[str],
    old_ytdlp: str,
) -> list[str]:
    steps = []
    packages = (["ffmpeg"] if want_ffmpeg else []) + apt_extra
    if packages:
        steps.append(f"Install with apt: {', '.join(packages)}")
    if download:
        verb = f"Update yt-dlp {old_ytdlp} to" if old_ytdlp else "Install yt-dlp"
        where = BUNDLE_DIR if download.shape == "bundle" else BIN
        steps.append(
            f"{verb} {download.tag}: download {download.asset}, check its SHA-256, "
            f"run it once to be sure it works here, then put it in {where}"
        )
    return steps


def build_tools_script(
    want_ffmpeg: bool, download: YtDlpDownload | None, apt_extra: list[str]
) -> str:
    """Needs passwordless sudo. Prints `ffmpeg=...` and `yt-dlp=...` when they were installed."""
    packages = (["ffmpeg"] if want_ffmpeg else []) + list(apt_extra)
    allowed = {"ffmpeg", "unzip"}
    if any(p not in allowed for p in packages):
        raise ToolError("Not an installable package")
    lines = [
        "set -e",
        'sudo -n true 2>/dev/null || { echo "passwordless sudo is required" >&2; exit 3; }',
    ]
    if packages:
        lines += [
            "sudo apt-get update",
            "sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y "
            + " ".join(shlex.quote(p) for p in packages),
        ]
    if want_ffmpeg:
        lines.append('echo "ffmpeg=$(ffmpeg -version | head -1)"')
    if download:
        check_tag(download.tag)
        sums = shlex.quote(download.sha256 + "  ")
        lines += [
            "tmp=$(mktemp -d); trap 'rm -rf \"$tmp\"' EXIT",
            f'curl -fsSL -o "$tmp/download" {shlex.quote(download.url)}',
            (
                f'echo {sums}"$tmp/download" | sha256sum -c - >/dev/null'
                ' || { echo "checksum mismatch: download refused" >&2; exit 4; }'
            ),
        ]
        if download.shape == "file":
            lines += [
                'chmod 755 "$tmp/download"',
                '"$tmp/download" --version >/dev/null || { echo "the downloaded yt-dlp does not run on this system: nothing was installed" >&2; exit 7; }',
                f'sudo install -m 755 "$tmp/download" {BIN}',
            ]
        else:
            launcher = shlex.quote(download.launcher)
            lines += [
                'mkdir "$tmp/x"; unzip -q "$tmp/download" -d "$tmp/x"',
                f'chmod 755 "$tmp/x/"{launcher}',
                f'"$tmp/x/"{launcher} --version >/dev/null || {{ echo "the downloaded yt-dlp does not run on this system: nothing was installed" >&2; exit 7; }}',
                f"sudo rm -rf {BUNDLE_DIR}.new",
                f'sudo mv "$tmp/x" {BUNDLE_DIR}.new',
                f"sudo chown -R root:root {BUNDLE_DIR}.new",
                f"sudo rm -rf {BUNDLE_DIR}",
                f"sudo mv {BUNDLE_DIR}.new {BUNDLE_DIR}",
                f"sudo ln -sf {BUNDLE_DIR}/{download.launcher} {BIN}",
            ]
        lines.append('echo "yt-dlp=$(yt-dlp --version)"')
    return "\n".join(lines) + "\n"
