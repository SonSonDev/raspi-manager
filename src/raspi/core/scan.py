"""Find the apps a Pi already runs: read-only script and parser (no I/O here)."""

from __future__ import annotations

import re
from dataclasses import dataclass

_MARK = "@@UNIT "
_ARGV = re.compile(r"argv\[\]=(.*?) ;")


@dataclass(frozen=True)
class Candidate:
    unit: str  # e.g. "example-bot.service"
    user: str
    state: str  # systemd ActiveState
    workdir: str
    repo: str  # git working copy containing the workdir, "" when there is none
    command: str

    @property
    def name(self) -> str:
        return self.unit.removesuffix(".service")


def build_scan_script() -> str:
    """Read-only: admin-written service files (real files, not aliases) with their
    settings. Unit files may be unreadable without sudo, so `systemctl show` is used."""
    return (
        "for f in /etc/systemd/system/*.service; do\n"
        '  [ -f "$f" ] && [ ! -L "$f" ] || continue\n'
        "  u=${f##*/}\n"
        f'  echo "{_MARK}$u"\n'
        '  systemctl show "$u" -p User -p ActiveState -p WorkingDirectory -p ExecStart 2>&1 | cut -c1-400\n'
        '  d=$(systemctl show "$u" -p WorkingDirectory --value 2>/dev/null)\n'
        '  if [ -n "$d" ] && [ -d "$d" ]; then\n'
        '    echo "repo=$(git -C "$d" rev-parse --show-toplevel 2>/dev/null)"\n'
        "  fi\n"
        "done\n"
    )


def parse_scan(output: str) -> list[Candidate]:
    found: list[Candidate] = []
    fields: dict[str, str] = {}
    unit = ""

    def flush() -> None:
        if unit:
            command = _ARGV.search(fields.get("ExecStart", ""))
            found.append(
                Candidate(
                    unit=unit,
                    user=fields.get("User", ""),
                    state=fields.get("ActiveState", "unknown"),
                    workdir=fields.get("WorkingDirectory", "").lstrip("!-"),
                    repo=fields.get("repo", ""),
                    command=command.group(1) if command else "",
                )
            )

    for raw in output.splitlines():
        line = raw.rstrip("\r")
        if line.startswith(_MARK):
            flush()
            unit, fields = line[len(_MARK) :].strip(), {}
        elif "=" in line:
            key, _, value = line.partition("=")
            fields[key] = value
    flush()
    return found
