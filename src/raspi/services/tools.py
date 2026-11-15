"""Install ffmpeg and yt-dlp on a Pi: read-only plan, then one confirmed script."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from raspi.core.models import Host
from raspi.core.tools import (
    FFMPEG,
    RELEASE_URL,
    TOOLS,
    YTDLP,
    ToolError,
    ToolFacts,
    YtDlpDownload,
    asset_for,
    build_probe_script,
    build_tools_script,
    check_tag,
    ffmpeg_short,
    needs_ytdlp,
    parse_probe,
    parse_sums,
    tools_steps,
)
from raspi.ssh.transport import Transport

LATEST = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"
HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "raspi-manager"}
Lookup = Callable[[str], YtDlpDownload]


def fetch_release(machine: str, http: httpx.Client | None = None) -> YtDlpDownload:
    """Latest official release and the SHA-256 of the file for this architecture.

    A plain public GET with a generic client name; nothing about the user is sent.
    """
    asset, shape = asset_for(machine)
    client = http or httpx.Client(timeout=15, follow_redirects=True)
    try:
        response = client.get(LATEST, headers=HEADERS)
        response.raise_for_status()
        tag = check_tag(str(response.json().get("tag_name", "")))
        sums = client.get(f"{RELEASE_URL}/{tag}/SHA2-256SUMS", headers=HEADERS)
        sums.raise_for_status()
    except (httpx.HTTPError, ValueError) as exc:
        raise ToolError(
            f"Cannot read the latest yt-dlp release from GitHub: {exc}"
        ) from exc
    sha = parse_sums(sums.text, asset)
    if sha is None:
        raise ToolError(f"The release {tag} has no checksum for {asset}")
    return YtDlpDownload(tag, asset, shape, sha)


@dataclass
class ToolState:
    """What the Pi has now, for the picker (read-only)."""

    facts: ToolFacts
    latest: str = ""  # latest yt-dlp tag, "" when GitHub could not be read

    def lines(self) -> dict[str, str]:
        f = self.facts
        ffmpeg = (
            f"installed ({ffmpeg_short(f.ffmpeg)})" if f.ffmpeg else "not installed"
        )
        if not f.ytdlp:
            ytdlp = "not installed"
        elif self.latest and f.ytdlp != self.latest:
            ytdlp = f"{f.ytdlp}, newer available ({self.latest})"
        else:
            ytdlp = f"{f.ytdlp}, up to date" if self.latest else f.ytdlp
        return {FFMPEG: ffmpeg, YTDLP: ytdlp}

    def needed(self) -> set[str]:
        f = self.facts
        out = set()
        if not f.ffmpeg:
            out.add(FFMPEG)
        if not f.ytdlp or (self.latest and f.ytdlp != self.latest):
            out.add(YTDLP)
        return out


def read_state(
    host: Host, transport: Transport, lookup: Lookup = fetch_release
) -> ToolState:
    probe = transport.run_script(host, build_probe_script())
    if not probe.ok:
        raise ToolError(f"Cannot inspect {host.name}: {probe.error}")
    facts = parse_probe(probe.stdout)
    try:
        latest = lookup(facts.machine).tag
    except ToolError:
        latest = ""
    return ToolState(facts, latest)


@dataclass
class ToolsPlan:
    wanted: set[str]
    steps: list[str]
    script: str
    facts: ToolFacts
    notes: list[str] = field(default_factory=list)

    @property
    def nothing_to_do(self) -> bool:
        return not self.steps


def plan_tools(
    host: Host, transport: Transport, wanted: set[str], lookup: Lookup = fetch_release
) -> ToolsPlan:
    """Read-only apart from the public GitHub lookup. Raises ToolError with the reason."""
    unknown = wanted - set(TOOLS)
    if unknown or not wanted:
        raise ToolError(f"Choose among: {', '.join(TOOLS)}")
    probe = transport.run_script(host, build_probe_script())
    if not probe.ok:
        raise ToolError(f"Cannot inspect {host.name}: {probe.error}")
    facts = parse_probe(probe.stdout)
    want_ffmpeg = FFMPEG in wanted and not facts.ffmpeg
    download = None
    notes = []
    if YTDLP in wanted:
        if not facts.has_curl:
            raise ToolError(f"curl is missing on {host.name}: run provision first")
        download = lookup(facts.machine)
        if not needs_ytdlp(facts, download.tag):
            download = None
            notes.append(f"yt-dlp {facts.ytdlp} is already the latest")
    if FFMPEG in wanted and facts.ffmpeg:
        notes.append(f"ffmpeg is already installed ({ffmpeg_short(facts.ffmpeg)})")
    apt_extra = (
        ["unzip"]
        if download and download.shape == "bundle" and not facts.has_unzip
        else []
    )
    if (want_ffmpeg or download) and not facts.has_sudo:
        raise ToolError(f"{host.name} needs passwordless sudo to install tools")
    steps = tools_steps(want_ffmpeg, download, apt_extra, facts.ytdlp)
    script = build_tools_script(want_ffmpeg, download, apt_extra) if steps else ""
    return ToolsPlan(wanted, steps, script, facts, notes)


@dataclass
class ToolsResult:
    ok: bool
    messages: list[str]


def install_tools(host: Host, transport: Transport, plan: ToolsPlan) -> ToolsResult:
    result = transport.run_script(host, plan.script, timeout=900)
    if not result.ok:
        return ToolsResult(False, [f"Install failed on {host.name}: {result.error}"])
    found = dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )
    messages = []
    if "ffmpeg" in found:
        messages.append(
            f"ffmpeg {ffmpeg_short(found['ffmpeg'])} installed on {host.name}"
        )
    if "yt-dlp" in found:
        messages.append(f"yt-dlp {found['yt-dlp']} installed on {host.name}")
    return ToolsResult(True, messages or [f"Done on {host.name}"])
