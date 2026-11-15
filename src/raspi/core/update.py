"""Updating an app's working copy to another revision, and rolling back (pure; no I/O).

An update checks out an exact commit (detached HEAD) instead of merging a branch: the same
mechanism serves a branch tip, a tag, a sha and a rollback, and what runs is always one
named commit. It refuses to run over uncommitted changes or over commits that exist only on
the Pi, so nothing the developer did there is lost. The build uses nvm's default Node.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field

from raspi.core.install import quoted_dir, work_dir
from raspi.core.models import AppRef

_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")
_SHA = re.compile(r"^[0-9a-f]{7,40}$")
MAX_COMMITS = 20


class UpdateError(Exception):
    """The app cannot be updated as asked."""


def check_ref(ref: str | None) -> str:
    """A branch, tag or sha typed by the user ('' = the default branch's latest commit)."""
    text = (ref or "").strip()
    if text and not _REF.match(text):
        raise UpdateError("A ref may only use letters, digits and . _ / -")
    return text


def rollback_target(app: AppRef, head: str) -> str:
    """The revision a rollback returns to, or '' when there is none.

    After a failed update the working copy is ahead of `built`: going back means returning to
    what was built. Otherwise it is the revision built before the last update.
    """
    built = app.built or ""
    if built and not (head.startswith(built) or built.startswith(head)):
        return built
    return app.previous or ""


def build_probe_script(app: AppRef, ref: str = "", sha: str = "") -> str:
    """Looks at the working copy and at GitHub. Only `git fetch` writes (the .git folder's
    remote-tracking refs): the working tree and the running app are not touched."""
    d = quoted_dir(app)
    unit = shlex.quote(app.unit)
    if sha:
        if not _SHA.match(sha):
            raise UpdateError("Not a revision")
        resolve = f"t=$(git rev-parse --verify --quiet {shlex.quote(sha)}'^{{commit}}')"
    elif ref:
        q = shlex.quote(ref)
        resolve = (
            f"for c in refs/remotes/origin/{q} refs/tags/{q} {q}; do\n"
            '  t=$(git rev-parse --verify --quiet "$c^{commit}") && break\n'
            "done"
        )
    else:
        resolve = (
            "t=$(git rev-parse --verify --quiet 'refs/remotes/origin/HEAD^{commit}')"
        )
    return (
        "\n".join(
            [
                f'cd {d} 2>/dev/null && [ -d .git ] || {{ echo "workdir=missing"; exit 0; }}',
                'echo "workdir=ok"',
                'echo "head=$(git rev-parse HEAD 2>/dev/null)"',
                'echo "dirty=$(git status --porcelain --untracked-files=no 2>/dev/null | wc -l)"',
                'echo "local=$(git rev-list HEAD --not --remotes 2>/dev/null | wc -l)"',
                (
                    "if GIT_TERMINAL_PROMPT=0 GIT_SSH_COMMAND='ssh -o BatchMode=yes' "
                    "git fetch --quiet --tags origin 2>/dev/null; "
                    'then echo "fetch=ok"; else echo "fetch=failed"; fi'
                ),
                "t=",
                resolve,
                'echo "target=$t"',
                'if [ -n "$t" ]; then',
                '  echo "behind=$(git rev-list --count HEAD..$t 2>/dev/null)"',
                f"  git log --format='log=%h %s' HEAD..$t 2>/dev/null | head -{MAX_COMMITS}",
                "fi",
                'echo "sudo=$(sudo -n true 2>/dev/null && echo yes || echo no)"',
                f'echo "load=$(systemctl show {unit} -p LoadState --value 2>/dev/null)"',
                f'echo "active=$(systemctl show {unit} -p ActiveState --value 2>/dev/null)"',
            ]
        )
        + "\n"
    )


@dataclass(frozen=True)
class UpdateFacts:
    workdir_ok: bool = False
    head: str = ""
    dirty: int = 0
    local_only: int = 0
    fetch_ok: bool = False
    target: str = ""
    behind: int = 0
    commits: list[str] = field(default_factory=list)
    has_sudo: bool = False
    load: str = ""
    active: str = ""


def _count(value: str) -> int:
    return int(value) if value.isdigit() else 0


def parse_probe(output: str) -> UpdateFacts:
    values: dict[str, str] = {}
    commits: list[str] = []
    for raw in output.splitlines():
        line = raw.rstrip("\r")
        if line.startswith("log="):
            commits.append("".join(c for c in line[4:] if c.isprintable()))
            continue
        key, sep, value = line.partition("=")
        if sep:
            values[key] = value.strip()
    return UpdateFacts(
        workdir_ok=values.get("workdir") == "ok",
        head=values.get("head", ""),
        dirty=_count(values.get("dirty", "")),
        local_only=_count(values.get("local", "")),
        fetch_ok=values.get("fetch") == "ok",
        target=values.get("target", ""),
        behind=_count(values.get("behind", "")),
        commits=commits,
        has_sudo=values.get("sudo") == "yes",
        load=values.get("load", ""),
        active=values.get("active", ""),
    )


def build_update_script(app: AppRef, sha: str) -> str:
    """Runs as the login user (no sudo). Prints `rev=<short commit>` on success."""
    if not _SHA.match(sha) or len(sha) != 40:
        raise UpdateError("The target must be a full commit hash")
    d = quoted_dir(app)
    lines = [
        "set -e",
        f"cd {d}",
        f"git checkout --detach --quiet {sha}",
        'export NVM_DIR="$HOME/.nvm"',
        '[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"',
    ]
    if app.build:
        lines.append(app.build)
    lines.append('echo "rev=$(git rev-parse --short HEAD)"')
    return "\n".join(lines) + "\n"


def update_steps(
    app: AppRef, old: str, new: str, behind: int, restart: bool, rollback: bool
) -> list[str]:
    verb = "Go back to" if rollback else "Check out"
    steps = [f"{verb} {new[:7]} (from {old[:7]}) in {work_dir(app)}"]
    if behind and not rollback:
        steps[0] += f", {behind} new commit(s)"
    if app.build:
        steps.append(f"Run in that folder: {app.build}")
    steps.append(
        f"Restart {app.unit}" if restart else "The app is not restarted (see the notes)"
    )
    steps.append("Record the new revision in ~/.raspi/apps.yaml")
    return steps
