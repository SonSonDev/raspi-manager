"""SSH transport: runs a script on a Pi through the system `ssh` binary.

We shell out instead of using a Python SSH library so the Windows OpenSSH agent,
known_hosts and ssh config that already work for the developer are reused as-is.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from raspi.core.models import Host

_NOISE = (
    "post-quantum",
    "store now, decrypt later",
    "openssh.com/pq",
    "The server may need to be upgraded",
)


_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # 0 on non-Windows

# The script travels on stdin. `sh -s` would read it line by line from that same stdin, so a
# command that reads stdin (a build tool, an installer) could swallow the rest of the script
# without any error. Reading it all first (`cat`) and evaluating it leaves stdin at EOF.
REMOTE_COMMAND = "sh -c 'eval \"$(cat)\"'"


@dataclass
class RunResult:
    ok: bool
    stdout: str
    error: str = ""


@dataclass
class ByteResult:
    ok: bool
    stdout: bytes
    error: str = ""


class Transport(Protocol):
    def run_script(
        self, host: Host, script: str, timeout: float = 20.0
    ) -> RunResult: ...


class ByteTransport(Transport, Protocol):
    """A transport that can also move binary data (a tar stream), relayed through this PC."""

    def run_bytes(
        self, host: Host, script: str, data: bytes = b"", timeout: float = 600.0
    ) -> ByteResult: ...


def find_ssh() -> str:
    # Prefer Windows' own OpenSSH: it is the one that talks to the Windows ssh-agent
    # (Git's bundled ssh cannot).
    system = (
        Path(os.environ.get("SystemRoot", r"C:\Windows"))
        / "System32"
        / "OpenSSH"
        / "ssh.exe"
    )
    if system.exists():
        return str(system)
    return shutil.which("ssh") or "ssh"


class SshTransport:
    def __init__(self, ssh_path: str | None = None) -> None:
        self._ssh = ssh_path or find_ssh()

    def run_script(self, host: Host, script: str, timeout: float = 20.0) -> RunResult:
        cmd = [
            self._ssh,
            "-o",
            "BatchMode=yes",  # never prompt: fail fast if the key is not in the agent
            "-o",
            "ConnectTimeout=8",
            "-o",
            "StrictHostKeyChecking=yes",  # unknown host keys must be accepted deliberately
            f"{host.user}@{host.address}",
            REMOTE_COMMAND,
        ]
        try:
            # Bytes, not text: text mode would turn "\n" into "\r\n" on Windows and break `sh`.
            proc = subprocess.run(
                cmd,
                check=False,
                input=script.encode(),
                capture_output=True,
                timeout=timeout,
                # The GUI has no console: without this flag Windows pops up a
                # console window for every ssh.exe call.
                creationflags=_NO_WINDOW,
            )
        except subprocess.TimeoutExpired:
            return RunResult(False, "", f"Timed out after {timeout:.0f}s")
        except OSError as exc:
            return RunResult(False, "", f"Cannot run ssh: {exc}")
        stdout = proc.stdout.decode(errors="replace")
        if proc.returncode != 0:
            return RunResult(
                False, stdout, _explain(proc.stderr.decode(errors="replace"))
            )
        return RunResult(True, stdout)

    def run_bytes(
        self, host: Host, script: str, data: bytes = b"", timeout: float = 600.0
    ) -> ByteResult:
        # stdin carries the data, so the script travels as the remote command instead.
        cmd = [
            self._ssh,
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=8",
            "-o",
            "StrictHostKeyChecking=yes",
            f"{host.user}@{host.address}",
            f"sh -c {shlex.quote(script)}",
        ]
        try:
            proc = subprocess.run(
                cmd,
                check=False,
                input=data,
                capture_output=True,
                timeout=timeout,
                creationflags=_NO_WINDOW,
            )
        except subprocess.TimeoutExpired:
            return ByteResult(False, b"", f"Timed out after {timeout:.0f}s")
        except OSError as exc:
            return ByteResult(False, b"", f"Cannot run ssh: {exc}")
        if proc.returncode != 0:
            return ByteResult(
                False, proc.stdout, _explain(proc.stderr.decode(errors="replace"))
            )
        return ByteResult(True, proc.stdout)


def _explain(stderr: str) -> str:
    text = " ".join(
        line.strip()
        for line in stderr.splitlines()
        if line.strip() and not any(n in line for n in _NOISE)
    )
    if "Permission denied" in text:
        return (
            text
            + " -- is the key loaded in ssh-agent? Run scripts/setup-pi-ssh.ps1 -CheckOnly."
        )
    if "Host key verification failed" in text:
        return text + " -- connect once with ssh to accept the host key."
    return text or "ssh failed"
