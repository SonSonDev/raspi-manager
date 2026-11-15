"""Installing Node.js on a Pi through nvm (pure; no I/O).

nvm is pinned to a release tag and its install script is checked against a SHA-256 taken
from that tag before it runs, so a moved tag or a corrupted download is refused.
"""

from __future__ import annotations

import re
import shlex

NVM_TAG = "v0.41.0"  # checked online 2026-11-15 (latest stable, GPG-signed tag)
NVM_SHA256 = "045932fe05f0d4247c946f2ec0ec4026dbfd8ce88a5f67ea28d8ea833227f48b"
NVM_URL = f"https://raw.githubusercontent.com/nvm-sh/nvm/{NVM_TAG}/install.sh"
NODE_LINES = ("22", "24", "26")  # LTS lines listed on nodejs.org, 2026-11-15
_MAJOR = re.compile(r"^\d{1,2}$")


class RuntimeError_(Exception):
    """The requested runtime cannot be installed."""


def check_major(value: str) -> str:
    value = str(value).strip()
    if not _MAJOR.match(value):
        raise RuntimeError_("The Node version must be a major number such as 22")
    return value


def install_steps(major: str, have_nvm: bool) -> list[str]:
    steps = []
    if not have_nvm:
        steps.append(
            f"Download the nvm {NVM_TAG} installer, check its SHA-256 and run it "
            "(clones nvm into ~/.nvm and adds its lines to the shell profile)"
        )
    steps.append(f"Install Node {major} with nvm and make it the default")
    return steps


def build_node_script(major: str, have_nvm: bool) -> str:
    """Runs as the login user (no sudo). Prints `node=<absolute path>` on success."""
    major = check_major(major)
    lines = ["set -e"]
    if not have_nvm:
        lines += [
            "tmp=$(mktemp -d); trap 'rm -rf \"$tmp\"' EXIT",
            f'curl -fsSL -o "$tmp/nvm-install.sh" {shlex.quote(NVM_URL)}',
            (
                f'echo {shlex.quote(NVM_SHA256 + "  ")}"$tmp/nvm-install.sh"'
                " | sha256sum -c - >/dev/null"
                ' || { echo "checksum mismatch: installer refused" >&2; exit 4; }'
            ),
            'bash "$tmp/nvm-install.sh"',
        ]
    lines += [
        'export NVM_DIR="$HOME/.nvm"',
        '. "$NVM_DIR/nvm.sh"',
        f"nvm install {major}",
        f"nvm alias default {major}",
        f'echo "node=$(nvm which {major})"',
    ]
    return "\n".join(lines) + "\n"
