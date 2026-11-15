"""Environment-file parsing, rendering and diffing. Pure functions; values are never logged."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SAFE_VALUE = re.compile(r"^[A-Za-z0-9_@%+=:,./-]*$")


class EnvError(ValueError):
    """Invalid key or value (message never contains the value)."""


@dataclass
class ParsedEnv:
    values: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)  # key names / line numbers only


def parse_env(text: str) -> ParsedEnv:
    """Parse dotenv / systemd EnvironmentFile syntax (KEY=value, optional `export`, quotes)."""
    result = ParsedEnv()
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not KEY_RE.match(key):
            result.warnings.append(f"line {number}: not a KEY=value line, ignored")
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = _unescape(value[1:-1])
        elif len(value) >= 2 and value[0] == value[-1] == "'":
            value = value[1:-1]
        elif " #" in value:
            # dotenv libraries drop this as a comment, systemd keeps it: behaviour differs.
            result.warnings.append(
                f"{key}: contains ' #' (comment or part of the value?)"
            )
        if key in result.values:
            result.warnings.append(f"{key}: defined more than once, last one wins")
        result.values[key] = value
    return result


def _unescape(value: str) -> str:
    out, i = [], 0
    while i < len(value):
        if value[i] == "\\" and i + 1 < len(value):
            out.append(
                {"n": "\n", "t": "\t", '"': '"', "\\": "\\"}.get(
                    value[i + 1], value[i : i + 2]
                )
            )
            i += 2
        else:
            out.append(value[i])
            i += 1
    return "".join(out)


def check_pair(key: str, value: str) -> None:
    if not KEY_RE.match(key):
        raise EnvError(
            f"Invalid variable name: {key!r} (letters, digits and _, not starting with a digit)"
        )
    if "\x00" in value:
        raise EnvError(f"Variable {key}: value contains a NUL character")


def render_env(values: dict[str, str]) -> str:
    """Render a file both systemd and dotenv readers understand (simple values raw, others quoted)."""
    lines = []
    for key, value in values.items():
        check_pair(key, value)
        if _SAFE_VALUE.match(value):
            lines.append(f"{key}={value}")
        else:
            escaped = (
                value.replace("\\", "\\\\")
                .replace('"', '\\"')
                .replace("\n", "\\n")
                .replace("\t", "\\t")
            )
            lines.append(f'{key}="{escaped}"')
    return "\n".join(lines) + ("\n" if lines else "")


@dataclass
class EnvDiff:
    added: list[str]  # in `new`, not in `old`
    removed: list[str]
    changed: list[str]

    @property
    def empty(self) -> bool:
        return not (self.added or self.removed or self.changed)

    def summary(self) -> str:
        if self.empty:
            return "identical"
        parts = []
        if self.added:
            parts.append(f"+{len(self.added)}")
        if self.changed:
            parts.append(f"~{len(self.changed)}")
        if self.removed:
            parts.append(f"-{len(self.removed)}")
        return " ".join(parts)


def diff_env(old: dict[str, str], new: dict[str, str]) -> EnvDiff:
    return EnvDiff(
        added=sorted(k for k in new if k not in old),
        removed=sorted(k for k in old if k not in new),
        changed=sorted(k for k in new if k in old and new[k] != old[k]),
    )


# --- templates (.env.example) -------------------------------------------------------
_SECRET_WORDS = (
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "PASSPHRASE",
    "TOKEN",
    "KEY",
    "PRIVATE",
)
_PLACEHOLDER = re.compile(
    r"^(change[-_ ]?me|replace[-_ ]?me|your[-_ ].*|<.*>|x{3,}|\*{3,}|todo|secret|password)$",
    re.IGNORECASE,
)
TEMPLATE_SUFFIXES = (".example", ".sample", ".template", ".dist")


def looks_secret(name: str) -> bool:
    upper = name.upper()
    return any(word in upper for word in _SECRET_WORDS)


def prefill_from_template(text: str) -> tuple[dict[str, str], list[str]]:
    """Variables of a template file: (values to start from, names that need a real value).

    Names that look secret and placeholder values (`change-me`, `<token>`...) start empty; the other
    defaults are kept as the project wrote them. Nothing is invented.
    """
    values: dict[str, str] = {}
    for name, value in parse_env(text).values.items():
        keep = (
            value and not looks_secret(name) and not _PLACEHOLDER.match(value.strip())
        )
        values[name] = value if keep else ""
    return values, sorted(name for name, value in values.items() if not value)


def empty_names(values: dict[str, str]) -> list[str]:
    return sorted(name for name, value in values.items() if not value)


def template_candidates(env_path: str) -> list[str]:
    """Where a template of `env_path` would be: `<file>.example`, `.sample`, `.template`, `.dist`."""
    return [env_path + suffix for suffix in TEMPLATE_SUFFIXES]
