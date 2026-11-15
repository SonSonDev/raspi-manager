"""Read-only deploy key for a private GitHub repo, created on the Pi (pure; no I/O).

One ed25519 key per app (`~/.ssh/raspi_deploy_<name>`, no passphrase so a deploy can run
unattended) and one ssh alias `github-<name>`, so each repo uses only its own key. GitHub's
host keys are written to `known_hosts` from the list GitHub publishes (checked by the PC
over HTTPS), never learned blindly from the first connection.
"""

from __future__ import annotations

import re
import shlex

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9._-]+$")
_KEY_LINE = re.compile(r"^[a-z0-9-]+ [A-Za-z0-9+/=]+$")


class DeployKeyError(Exception):
    """Unusable input for a deploy key."""


def check_name(name: str) -> str:
    name = str(name).strip()
    if not _NAME.match(name):
        raise DeployKeyError("The app name may only use letters, digits, . _ -")
    return name


def check_repo(repo: str) -> str:
    """`owner/name` (a trailing .git is accepted and dropped)."""
    repo = str(repo).strip().removesuffix(".git")
    if not _REPO.match(repo) or ".." in repo:
        raise DeployKeyError("The repository must look like owner/name")
    return repo


def check_github_keys(keys: list[str]) -> list[str]:
    clean = [k.strip() for k in keys if _KEY_LINE.match(k.strip())]
    if not clean:
        raise DeployKeyError("No usable GitHub host key to trust")
    return clean


def alias_for(name: str) -> str:
    return f"github-{check_name(name)}"


def key_path_for(name: str) -> str:
    return f"~/.ssh/raspi_deploy_{check_name(name)}"


def clone_url(name: str, repo: str) -> str:
    return f"git@{alias_for(name)}:{check_repo(repo)}.git"


def key_steps(name: str, host_name: str) -> list[str]:
    return [
        (
            f"Create the key {key_path_for(name)} on {host_name} if it does not "
            "exist (ed25519, no passphrase)"
        ),
        f"Add the ssh alias {alias_for(name)} to ~/.ssh/config (only that key is offered)",
        "Add GitHub's published host keys to ~/.ssh/known_hosts if github.com is not there",
    ]


def build_key_script(name: str, host_name: str, github_keys: list[str]) -> str:
    """Creates the key and ssh settings when missing; prints `pub=<public key>`."""
    name = check_name(name)
    keys = check_github_keys(github_keys)
    marker = f"# raspi-manager deploy key: {name}"
    block = (
        f"Host {alias_for(name)}\n"
        "  HostName github.com\n"
        "  User git\n"
        f"  IdentityFile {key_path_for(name)}\n"
        "  IdentitiesOnly yes\n"
    )
    known = "".join(f"github.com {k}\n" for k in keys)
    return (
        "\n".join(
            [
                "set -e",
                "umask 077",
                'd="$HOME/.ssh"',
                f'k="$d/raspi_deploy_{name}"',
                'mkdir -p "$d"; chmod 700 "$d"',
                (
                    '[ -f "$k" ] || ssh-keygen -q -t ed25519 -N "" '
                    f'-C {shlex.quote(f"raspi-{name}@{host_name}")} -f "$k"'
                ),
                'touch "$d/config" "$d/known_hosts"; chmod 600 "$d/config" "$d/known_hosts"',
                (
                    f'grep -qxF {shlex.quote(marker)} "$d/config" || '
                    f"printf '\\n%s\\n%s' {shlex.quote(marker)} {shlex.quote(block)}"
                    ' >> "$d/config"'
                ),
                (
                    'ssh-keygen -F github.com -f "$d/known_hosts" >/dev/null || '
                    f'printf %s {shlex.quote(known)} >> "$d/known_hosts"'
                ),
                'echo "pub=$(cat "$k.pub")"',
            ]
        )
        + "\n"
    )


def parse_public_key(output: str) -> str:
    for line in output.splitlines():
        if line.startswith("pub="):
            return line[4:].strip()
    return ""


def build_verify_script(name: str, repo: str) -> str:
    """Read-only: can the Pi reach the repo with its deploy key?"""
    url = shlex.quote(clone_url(name, repo))
    return (
        "GIT_TERMINAL_PROMPT=0 GIT_SSH_COMMAND='ssh -o BatchMode=yes' "
        f"git ls-remote --exit-code {url} HEAD >/dev/null\n"
        'echo "reachable"\n'
    )
