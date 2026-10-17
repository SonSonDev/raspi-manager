"""Local dashboard. Bound to 127.0.0.1 by `raspi web`.

Mutations (POST) need a per-process token and a localhost Host header: this stops
other web pages from driving the dashboard (CSRF / DNS rebinding) even though it
only listens on loopback.
"""

from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates

from raspi import APP_NAME
from raspi.config.loader import ConfigError, load_hosts
from raspi.core.remote import ACTIONS
from raspi.services.actions import LookupError_, find_app, read_logs, run_action
from raspi.services.status import collect_all
from raspi.ssh.transport import SshTransport, Transport

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
GLYPH = {"running": "●", "stopped": "○", "failed": "▲", "working": "◌", "unknown": "?"}
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


def _hostname(header: str) -> str:
    """Host header without its port; understands '[::1]:8765'."""
    if header.startswith("["):
        return header.split("]")[0] + "]"
    return header.split(":")[0]


def create_app(transport: Transport | None = None) -> FastAPI:
    app = FastAPI(title=APP_NAME, docs_url=None, redoc_url=None)
    ssh = transport or SshTransport()
    token = secrets.token_urlsafe(24)

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        if _hostname(request.headers.get("host", "")) not in _LOCAL_HOSTS:
            return PlainTextResponse("Forbidden host", status_code=403)
        return await call_next(request)

    def lookup(host_name: str, app_name: str):
        try:
            return find_app(load_hosts(), host_name, app_name)
        except (LookupError_, ConfigError) as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/", response_class=HTMLResponse)
    def fleet(request: Request) -> HTMLResponse:
        try:
            statuses = collect_all(load_hosts(), ssh)
            error = ""
        except ConfigError as exc:
            statuses, error = [], str(exc)
        return _TEMPLATES.TemplateResponse(
            request,
            "fleet.html",
            {"statuses": statuses, "error": error, "glyph": GLYPH, "token": token},
        )

    @app.get(
        "/apps/{host_name}/{app_name}/confirm/{action}", response_class=HTMLResponse
    )
    def confirm(
        request: Request, host_name: str, app_name: str, action: str
    ) -> HTMLResponse:
        if action not in ("stop", "restart"):
            raise HTTPException(404, "No confirmation needed for this action")
        host, ref = lookup(host_name, app_name)
        return _TEMPLATES.TemplateResponse(
            request,
            "confirm.html",
            {"host": host, "app": ref, "action": action, "token": token},
        )

    @app.post("/apps/{host_name}/{app_name}/{action}", response_class=HTMLResponse)
    def act(
        request: Request,
        host_name: str,
        app_name: str,
        action: str,
        form_token: str = Form(alias="token", default=""),
    ) -> HTMLResponse:
        if action not in ACTIONS:
            raise HTTPException(404, "Unknown action")
        if not secrets.compare_digest(form_token, token):
            raise HTTPException(403, "Invalid or missing token: reload the dashboard")
        host, ref = lookup(host_name, app_name)
        result = run_action(host, ref, action, ssh)
        return _TEMPLATES.TemplateResponse(
            request,
            "result.html",
            {"host": host, "app": ref, "action": action, "result": result},
        )

    @app.get("/apps/{host_name}/{app_name}/logs", response_class=HTMLResponse)
    def logs(
        request: Request, host_name: str, app_name: str, lines: int = 200
    ) -> HTMLResponse:
        host, ref = lookup(host_name, app_name)
        lines = max(1, min(lines, 1000))
        return _TEMPLATES.TemplateResponse(
            request,
            "logs.html",
            {
                "host": host,
                "app": ref,
                "lines": lines,
                "result": read_logs(host, ref, ssh, lines),
            },
        )

    return app
