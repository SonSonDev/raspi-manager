"""One long change at a time per Pi: a second click says so instead of failing on a lock.

The GUI is single-threaded, so a plain module-level set is enough.
"""

from __future__ import annotations

_RUNNING: set[str] = set()


def begin(host_name: str) -> bool:
    """True when the caller may start; False when a change is already running on that Pi."""
    if host_name in _RUNNING:
        return False
    _RUNNING.add(host_name)
    return True


def end(host_name: str) -> None:
    _RUNNING.discard(host_name)


def running(host_name: str) -> bool:
    return host_name in _RUNNING


MESSAGE = (
    "A change is already running on {host}: wait for it to finish (installing packages "
    "can take several minutes), then try again."
)
