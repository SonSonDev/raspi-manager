"""Create a deploy key on a Pi and check that GitHub accepts it."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import httpx

from raspi.core.deploykey import (
    DeployKeyError,
    build_key_script,
    build_verify_script,
    check_name,
    check_repo,
    clone_url,
    parse_public_key,
)
from raspi.core.deploykey_manage import (
    KeyInfo,
    build_delete_script,
    build_list_script,
    build_rotate_script,
    parse_list,
)
from raspi.core.models import Host
from raspi.ssh.transport import Transport

META_URL = "https://api.github.com/meta"


def fetch_github_keys() -> list[str]:
    """GitHub's published SSH host keys (`type base64`), over HTTPS."""
    try:
        response = httpx.get(META_URL, timeout=10)
        response.raise_for_status()
        return list(response.json().get("ssh_keys", []))
    except (httpx.HTTPError, ValueError) as exc:
        raise DeployKeyError(
            f"Cannot read GitHub's host keys from {META_URL}: {exc}"
        ) from exc


@dataclass
class KeyResult:
    ok: bool
    message: str
    public_key: str = ""
    script: str = ""


def ensure_key(
    host: Host,
    transport: Transport,
    name: str,
    dry_run: bool = False,
    lookup: Callable[[], list[str]] = fetch_github_keys,
) -> KeyResult:
    """Idempotent: an existing key and settings are left as they are."""
    try:
        script = build_key_script(check_name(name), host.name, lookup())
    except DeployKeyError as exc:
        return KeyResult(False, str(exc))
    if dry_run:
        return KeyResult(True, "Dry run: nothing was changed.", script=script)
    result = transport.run_script(host, script)
    if not result.ok:
        return KeyResult(
            False,
            f"Cannot prepare the key on {host.name}: {result.error}",
            script=script,
        )
    key = parse_public_key(result.stdout)
    if not key:
        return KeyResult(
            False, f"{host.name} did not return a public key", script=script
        )
    return KeyResult(True, f"Deploy key ready on {host.name}.", key, script)


def verify_repo(host: Host, transport: Transport, name: str, repo: str) -> KeyResult:
    """Read-only (`git ls-remote`): does GitHub accept the key for that repo?"""
    try:
        script = build_verify_script(check_name(name), check_repo(repo))
    except DeployKeyError as exc:
        return KeyResult(False, str(exc))
    result = transport.run_script(host, script, timeout=30)
    if result.ok:
        return KeyResult(True, f"{host.name} can read {check_repo(repo)}.")
    hint = ""
    if "Permission denied" in result.error or "Could not read" in result.error:
        hint = (
            f" The key is probably not added yet: on GitHub open the repository, "
            f"Settings, Deploy keys, Add deploy key (read-only), paste the key "
            f"({clone_url(name, repo)})."
        )
    return KeyResult(False, f"GitHub refused or is unreachable: {result.error}{hint}")


@dataclass
class KeyListing:
    ok: bool
    message: str = ""
    keys: list[KeyInfo] | None = None


def list_keys(host: Host, transport: Transport) -> KeyListing:
    """Read-only: the raspi deploy keys found in the login user's ~/.ssh."""
    result = transport.run_script(host, build_list_script())
    if not result.ok:
        return KeyListing(False, f"Cannot list the keys on {host.name}: {result.error}")
    return KeyListing(True, keys=parse_list(result.stdout))


def rotate_key(
    host: Host,
    transport: Transport,
    name: str,
    dry_run: bool = False,
    lookup: Callable[[], list[str]] = fetch_github_keys,
) -> KeyResult:
    """Replace the key of `name` by a new one; the old public key stops working at once."""
    try:
        script = build_rotate_script(check_name(name), host.name, lookup())
    except DeployKeyError as exc:
        return KeyResult(False, str(exc))
    if dry_run:
        return KeyResult(True, "Dry run: nothing was changed.", script=script)
    result = transport.run_script(host, script)
    key = parse_public_key(result.stdout) if result.ok else ""
    if not key:
        reason = result.error if not result.ok else "no public key returned"
        return KeyResult(
            False, f"Cannot rotate the key on {host.name}: {reason}", script=script
        )
    return KeyResult(True, f"New deploy key made on {host.name}.", key, script)


def delete_key(
    host: Host, transport: Transport, name: str, dry_run: bool = False
) -> KeyResult:
    """Delete the key files and alias block of `name`; nothing else."""
    try:
        script = build_delete_script(check_name(name))
    except DeployKeyError as exc:
        return KeyResult(False, str(exc))
    if dry_run:
        return KeyResult(True, "Dry run: nothing was changed.", script=script)
    result = transport.run_script(host, script)
    if not result.ok:
        return KeyResult(
            False,
            f"Cannot delete the key on {host.name}: {result.error}",
            script=script,
        )
    return KeyResult(
        True, f"Deploy key of {name} deleted on {host.name}.", script=script
    )
