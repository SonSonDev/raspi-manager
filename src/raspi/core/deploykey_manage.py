"""Managing the deploy keys already on a Pi: list, rotate, delete (pure; no I/O).

A key belongs to raspi-manager when its file is `~/.ssh/raspi_deploy_<name>` or its ssh alias
block carries our marker. The repository is not stored next to the key: it is read from the app of
the same name in the inventory, and a key with no such app is reported as unused (never deleted
without being asked).
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass

from raspi.core.deploykey import _NAME, build_key_script, check_name
from raspi.core.remove import KEY_MARKER, key_removal_lines


@dataclass(frozen=True)
class KeyInfo:
    name: str
    fingerprint: str = ""
    public_key: str = ""
    has_files: bool = False
    has_block: bool = False

    @property
    def complete(self) -> bool:
        return self.has_files and self.has_block


def build_list_script() -> str:
    """Read-only: one `key|name|fingerprint|public key` line per key file, one `block|name` per alias."""
    return (
        "\n".join(
            [
                'd="$HOME/.ssh"',
                'for p in "$d"/raspi_deploy_*.pub; do',
                '  [ -f "$p" ] || continue',
                '  n="${p##*/raspi_deploy_}"; n="${n%.pub}"',
                '  [ -f "$d/raspi_deploy_$n" ] || continue',
                '  fp="$(ssh-keygen -lf "$p" 2>/dev/null | cut -d" " -f2)"',
                '  echo "key|$n|$fp|$(cat "$p")"',
                "done",
                (
                    f'grep -F {shlex.quote(KEY_MARKER)} "$d/config" 2>/dev/null | '
                    f"sed 's/^{KEY_MARKER}/block|/' || true"
                ),
            ]
        )
        + "\n"
    )


def parse_list(output: str) -> list[KeyInfo]:
    files: dict[str, tuple[str, str]] = {}
    blocks: set[str] = set()
    for raw in output.splitlines():
        kind, _, rest = raw.rstrip("\r").partition("|")
        if kind == "key":
            name, _, rest = rest.partition("|")
            fingerprint, _, public = rest.partition("|")
            files[name] = (fingerprint.strip(), public.strip())
        elif kind == "block" and rest.strip():
            blocks.add(rest.strip())
    infos = []
    for name in sorted(set(files) | blocks):
        if not _NAME.match(
            name
        ):  # a foreign name in the file is skipped, never trusted
            continue
        fingerprint, public = files.get(name, ("", ""))
        infos.append(KeyInfo(name, fingerprint, public, name in files, name in blocks))
    return infos


def build_rotate_script(name: str, host_name: str, github_keys: list[str]) -> str:
    """Throws the old key away and makes a new one; the alias and known hosts are kept or restored."""
    name = check_name(name)
    return (
        "set -e\n"
        f'rm -f -- "$HOME/.ssh/raspi_deploy_{name}" "$HOME/.ssh/raspi_deploy_{name}.pub"\n'
        + build_key_script(name, host_name, github_keys)
    )


def build_delete_script(name: str) -> str:
    """The key files and the alias block of one app; nothing else on the Pi."""
    name = check_name(name)
    return "\n".join(["set -e", *key_removal_lines(name), 'echo "removed"']) + "\n"


def rotate_steps(name: str, host_name: str) -> list[str]:
    return [
        f"Delete the current key of {name} on {host_name}: its public key stops working on GitHub",
        "Create a new ed25519 key in its place (no passphrase)",
        "Keep (or restore) the ssh alias and GitHub's host keys",
        "Then add the new public key on GitHub and delete the old one there",
    ]


def delete_steps(name: str, host_name: str) -> list[str]:
    return [
        f"Delete ~/.ssh/raspi_deploy_{name} and its .pub on {host_name}",
        f"Remove the ssh alias github-{name} from ~/.ssh/config",
        (
            "The app, its working copy and its inventory entry are not touched; the key "
            "on GitHub stays until you delete it there"
        ),
    ]
