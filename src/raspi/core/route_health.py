"""Per-route health of an app (pure): which of its public routes answer locally."""

from __future__ import annotations

from raspi.core.models import AppRef, AppRoute, AppStatus, Host


def route_answers(code: int) -> bool:
    """Any HTTP answer below 500 means the port is served (a 404 on the check path still counts)."""
    return 0 < code < 500


def route_states(
    app: AppRef, status: AppStatus | None
) -> list[tuple[AppRoute, bool | None]]:
    """Each route with True (answers), False (does not) or None (not read yet)."""
    if status is None:
        return [(r, None) for r in app.routes]
    return [
        (
            r,
            route_answers(status.route_codes[i])
            if i < len(status.route_codes)
            else None,
        )
        for i, r in enumerate(app.routes)
    ]


def route_summary(app: AppRef, status: AppStatus | None) -> str:
    """`share.example.com :8788 up, main.example.com :8787 down`; empty without routes."""
    words = {True: "up", False: "down", None: "unknown"}
    return ", ".join(
        f"{r.hostname} :{r.port} {words[ok]}" for r, ok in route_states(app, status)
    )


def down_routes(app: AppRef, status: AppStatus | None) -> list[AppRoute]:
    return [r for r, ok in route_states(app, status) if ok is False]


def route_warnings(host: Host, app: AppRef, route: AppRoute) -> list[str]:
    """Overlaps with the other apps of this Pi (warnings, never a refusal)."""
    lines = []
    for other in host.apps:
        if other.name == app.name:
            continue
        for r in other.routes:
            if r.hostname == route.hostname:
                lines.append(f"{route.hostname} is already a route of {other.name}")
            elif r.port == route.port:
                lines.append(
                    f"Port {route.port} is also the port of {other.name} ({r.hostname})"
                )
    mine = [
        r for r in app.routes if r.hostname != route.hostname and r.port == route.port
    ]
    lines += [
        f"Port {route.port} is also used by this app's route {r.hostname}" for r in mine
    ]
    return lines
