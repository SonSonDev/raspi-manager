"""Cloning an app's repo on a Pi and running its build command (pure; no I/O).

The clone goes through the app's deploy-key alias (see `core.deploykey`). An existing
working copy is never touched (no pull, no checkout: updating a version is the deploy step),
so running the install twice is safe. The build runs with the default Node of nvm when nvm
is installed.
"""

from __future__ import annotations

import shlex

from raspi.core.deploykey import clone_url, key_path_for
from raspi.core.models import AppRef


class InstallError(Exception):
    """The app cannot be installed as declared."""


def work_dir(app: AppRef) -> str:
    """Absolute folder on the Pi (the app's `path`, else `~/apps/<name>`)."""
    return app.path or f"$HOME/apps/{app.name}"


def _quoted_dir(app: AppRef) -> str:
    # $HOME must stay expandable, so only the part after it is quoted
    if app.path:
        return shlex.quote(app.path)
    return f'"$HOME/apps/{app.name}"'


def build_probe_script(app: AppRef) -> str:
    """Read-only: is the deploy key there, and what is at the target folder?"""
    key = key_path_for(app.name).replace("~", "$HOME", 1)
    d = _quoted_dir(app)
    return (
        f'echo "key=$([ -f "{key}" ] && echo yes || echo no)"\n'
        f"if [ -d {d}/.git ]; then echo state=repo\n"
        f'elif [ -e {d} ] && [ -n "$(ls -A {d} 2>/dev/null)" ]; then echo state=other\n'
        "else echo state=missing; fi\n"
        'echo "disk_mb=$(df -Pm "$HOME" 2>/dev/null | awk \'NR==2{print $4}\')"\n'
    )


def parse_probe(output: str) -> dict[str, str]:
    values = {}
    for raw in output.splitlines():
        key, sep, value = raw.rstrip("\r").partition("=")
        if sep:
            values[key] = value.strip()
    return values


def install_steps(app: AppRef, state: str) -> list[str]:
    steps = []
    if state == "missing":
        steps.append(f"Clone {app.repo} into {work_dir(app)} (through the deploy key)")
    else:
        steps.append(f"Keep the existing working copy {work_dir(app)} as it is")
    if app.build:
        steps.append(f"Run in that folder: {app.build}")
    return steps


def build_install_script(app: AppRef, state: str) -> str:
    """Runs as the login user (no sudo). Prints `rev=<short commit>` on success."""
    if not app.repo:
        raise InstallError(f"{app.name} has no repo declared")
    d = _quoted_dir(app)
    lines = ["set -e"]
    if state == "missing":
        lines += [
            f'mkdir -p "$(dirname {d})"',
            (
                "GIT_TERMINAL_PROMPT=0 GIT_SSH_COMMAND='ssh -o BatchMode=yes' "
                f"git clone {shlex.quote(clone_url(app.name, app.repo))} {d}"
            ),
        ]
    elif state != "repo":
        raise InstallError(f"{work_dir(app)} exists and is not a git working copy")
    lines += [
        f"cd {d}",
        'export NVM_DIR="$HOME/.nvm"',
        '[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"',
    ]
    if app.build:
        lines.append(app.build)
    lines.append('echo "rev=$(git rev-parse --short HEAD)"')
    return "\n".join(lines) + "\n"
