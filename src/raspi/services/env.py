"""Read, compare and push an app's environment files. Values are never logged or put in messages."""

from __future__ import annotations

from dataclasses import dataclass

from raspi.core.env import ParsedEnv, parse_env, prefill_from_template, render_env
from raspi.core.models import EnvFile, Host
from raspi.core.remote import (
    EnvScan,
    build_env_detect_script,
    build_env_push_script,
    build_env_read_script,
    build_template_read_script,
    parse_env_read,
    parse_env_scan,
    parse_template_read,
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


def scan_env(host: Host, folder: str, transport: Transport) -> EnvScan | None:
    """Env files and templates under the app folder (read-only). None if the folder is missing.

    `folder` may be `$HOME/apps/<app>` for an app without a declared path.
    """
    result = transport.run_script(host, build_env_detect_script(folder))
    if not result.ok:
        raise RuntimeError(result.error)
    return parse_env_scan(folder, result.stdout)


def detect_env_files(host: Host, folder: str, transport: Transport) -> list[str] | None:
    """Candidate env files under the app folder, templates excluded."""
    scan = scan_env(host, folder, transport)
    return None if scan is None else scan.files


@dataclass
class TemplateValues:
    path: str  # the template that was read, e.g. .../.env.example
    values: dict[
        str, str
    ]  # variable -> value to start from (secret-looking ones empty)
    todo: list[str]  # names that still need a real value


def read_template(
    host: Host, env_path: str, transport: Transport
) -> TemplateValues | None:
    """Read-only: variables of the template next to `env_path`, or None when there is none."""
    result = transport.run_script(host, build_template_read_script(env_path))
    if not result.ok:
        raise RuntimeError(result.error)
    found = parse_template_read(result.stdout)
    if found is None:
        return None
    path, text = found
    values, todo = prefill_from_template(text)
    return TemplateValues(path, values, todo)


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
