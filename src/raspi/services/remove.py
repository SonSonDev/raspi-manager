"""Remove an app from a Pi: plan (read-only), then apply only what was ticked."""

from __future__ import annotations

from dataclasses import dataclass, field

from raspi.config import envstore
from raspi.core.models import AppRef, Host
from raspi.core.remove import (
    ENV,
    INVENTORY,
    LOCAL,
    Item,
    RemoveFacts,
    available_items,
    build_probe_script,
    build_remove_script,
    parse_probe,
)
from raspi.services.adopt import remove_app
from raspi.ssh.transport import Transport


class RemoveError(Exception):
    """The Pi could not be inspected or the selection is not allowed."""


@dataclass
class RemovePlan:
    app: AppRef
    facts: RemoveFacts
    items: list[Item]

    def script_for(self, chosen: set[str]) -> str:
        return build_remove_script(self.app, chosen, self.facts)

    def allowed(self) -> set[str]:
        return {i.key for i in self.items if i.available}


def plan_remove(
    host: Host, transport: Transport, app: AppRef, inventory_on_pi: bool
) -> RemovePlan:
    """Read-only. Lists what exists and what can be removed, with the reason when it cannot."""
    probe = transport.run_script(host, build_probe_script(app))
    if not probe.ok:
        raise RemoveError(f"Cannot inspect {host.name}: {probe.error}")
    facts = parse_probe(probe.stdout, len(app.env_files))
    labels = [e.label for e in app.env_files]
    local = bool(envstore.local_copies(host.name, app.name, labels))
    return RemovePlan(app, facts, available_items(app, facts, inventory_on_pi, local))


@dataclass
class RemoveResult:
    ok: bool
    messages: list[str] = field(default_factory=list)


def apply_remove(
    host: Host, transport: Transport, plan: RemovePlan, chosen: set[str]
) -> RemoveResult:
    """Pi first, then the local copies, the inventory entry last: a failure leaves the app listed."""
    refused = chosen - plan.allowed()
    if refused:
        raise RemoveError(f"Not available: {', '.join(sorted(refused))}")
    app, messages = plan.app, []
    if chosen - {INVENTORY, LOCAL}:
        script = plan.script_for(chosen)
        result = transport.run_script(host, script, timeout=120)
        if not result.ok:
            return RemoveResult(
                False,
                [
                    f"Removal failed on {host.name}: {result.error}",
                    "The app is still listed.",
                ],
            )
        messages.append(
            f"Removed on {host.name}: " + ", ".join(sorted(chosen - {INVENTORY, LOCAL}))
        )
    if LOCAL in chosen:
        count = envstore.delete_local(
            host.name, app.name, [e.label for e in app.env_files]
        )
        messages.append(f"Deleted {count} local environment copy(ies) on this PC")
    if INVENTORY in chosen:
        done = remove_app(host, app.name, transport)
        if not done.ok:
            return RemoveResult(
                False, messages + [f"But the list was not updated: {done.message}"]
            )
        messages.append(done.message)
    return RemoveResult(True, messages)


__all__ = [
    "ENV",
    "RemoveError",
    "RemovePlan",
    "RemoveResult",
    "apply_remove",
    "plan_remove",
]
