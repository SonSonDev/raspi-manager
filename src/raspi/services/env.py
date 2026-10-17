"""Read, compare and push an app's environment files. Values are never logged or put in messages."""

from __future__ import annotations

from dataclasses import dataclass

from raspi.core.env import ParsedEnv, parse_env, render_env
from raspi.core.models import EnvFile, Host
from raspi.core.remote import (
    build_env_detect_script,
    build_env_push_script,
    build_env_read_script,
    parse_env_detect,
    parse_env_read,
)
from raspi.ssh.transport import Transport


@dataclass
class RemoteEnv:
    ok: bool
    exists: bool = False
    parsed: ParsedEnv | None = None
    error: str = ""


@dataclass
class PushResult:
    ok: bool
    message: str
    executed: bool = True
    script_bytes: int = 0  # size only, the script itself contains the secrets


def read_remote(host: Host, env: EnvFile, transport: Transport) -> RemoteEnv:
    result = transport.run_script(host, build_env_read_script(env.path))
    if not result.ok:
        return RemoteEnv(False, error=result.error)
    content = parse_env_read(result.stdout)
    if content is None:
        return RemoteEnv(True, exists=False, parsed=ParsedEnv())
    return RemoteEnv(True, exists=True, parsed=parse_env(content))


def detect_env_files(host: Host, folder: str, transport: Transport) -> list[str] | None:
    """Candidate env files under the app folder (read-only). None if the folder is missing."""
    result = transport.run_script(host, build_env_detect_script(folder))
    if not result.ok:
        raise RuntimeError(result.error)
    return parse_env_detect(folder, result.stdout)


def push(
    host: Host,
    env: EnvFile,
    values: dict[str, str],
    transport: Transport,
    dry_run: bool = False,
) -> PushResult:
    content = render_env(values)  # validates keys and values
    script = build_env_push_script(env.path, content)
    if dry_run:
        return PushResult(
            True,
            "Dry run: nothing was written.",
            executed=False,
            script_bytes=len(script),
        )
    result = transport.run_script(host, script)
    if not result.ok:
        return PushResult(False, result.error, script_bytes=len(script))
    return PushResult(
        True, result.stdout.strip() or "written", script_bytes=len(script)
    )
