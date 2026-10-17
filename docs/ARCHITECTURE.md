# Architecture — code map
Updated whenever a structural element is added, moved or removed. Principles live in CLAUDE.md. Implemented so far (slices 1-2): `core/models.py`, `core/remote.py` (status/action/logs scripts + parsers; actions whitelisted to start/stop/restart), `config/loader.py`, `ssh/transport.py` (system `ssh` via subprocess), `services/status.py`, `services/actions.py` (`run_action` with dry-run, `read_logs`, `find_app`), `cli/main.py` (`status`, `start`, `stop`, `restart`, `logs`, `web`), `web/app.py` (token + Host-header check) with templates `base`, `fleet`, `confirm`, `result`, `logs`, tests `test_status.py` and `test_actions.py`. Native UI (slice 3): `gui/theme.py` (tokens → QSS), `gui/workers.py` (`submit`: QRunnable jobs, results delivered by queued signals), `gui/fleet_model.py` (QAbstractTableModel, one row per configured app, pending/unreachable/checking states), `gui/main_window.py` (toolbar, table, logs dock, 10 s auto-refresh, one refresh job per host), `gui/app.py` (`raspi gui`, follows Windows light/dark), `tests/test_gui.py` (offscreen). Environment (slice 4): `core/env.py` (parse/render/diff, NAMES-only messages), `config/envstore.py` (`~/.raspi/env/<host>.<app>.env`), `core/remote.py` env read/push scripts, `services/env.py` (`read_remote`, `push`, dry-run), `gui/env_dialog.py`, `tests/test_env.py`. Declaring env files (slice 5): `config/writer.py` (add/remove env file declarations in hosts.yaml, validated, `.bak` copy), `core/remote.py` detect script/parser, `core/naming.py` (label suggestions), `services/env.detect_env_files`, `gui/add_env_dialog.py`, `tests/test_declare.py`. The rest below is planned.

## Layout
```
src/raspi/
  core/        pure logic: models (Host, App, Route, Release), manifest/inventory parsing,
               systemd unit rendering, release planning, route planning. No I/O.
  ssh/         transport interface + Fabric/Paramiko implementation; fake transport for tests.
  cloudflare/  httpx client: tunnels, ingress config, DNS records.
  services/    use cases (deploy, rollback, lifecycle, env push, route sync, doctor) combining core + ssh + cloudflare.
  config/      loading/saving ~/.raspi/ (hosts.yaml, apps/*.yaml, env/*.env, secrets).
  cli/         Typer commands, thin wrappers over services.
  web/         FastAPI app, Jinja2 templates, HTMX partials, static assets (see DESIGN.md).
tests/         unit tests on core; services with fake transport.
docs/          BRIEF, SPEC, ARCHITECTURE, DESIGN, DECISIONS, QUESTIONS.
```
Dependencies point inward: `cli`/`web` → `services` → `core`; `services` → `ssh`, `cloudflare`, `config`.

## On the Pi (per app)
```
~/apps/<app>/releases/<sha>/    checked-out code + venv or node_modules
~/apps/<app>/current -> releases/<sha>
~/apps/<app>/shared/            persistent data (kept across releases)
/etc/systemd/system/raspi-<app>.service   (User=<login user>, EnvironmentFile=/etc/raspi/<app>.env, chmod 600)
/etc/systemd/system/cloudflared.service   (tunnel connector; config /etc/cloudflared/config.yml)
```

## Data flow
- Deploy: CLI/web → `services.deploy` → `core` plans steps → `ssh` runs them (fetch → checkout → install → switch link → restart) → status re-read from systemd.
- Status: web/CLI → `services.status` → parallel SSH per host (timeout) → `systemctl show`, `git rev-parse` → rendered with "last checked".
- Route: `services.route` → `core` builds ingress list (catch-all last) → `cloudflare` updates tunnel config + DNS → dashboard shows URL.

## Conventions
- Every mutating service takes a `dry_run` flag and returns a plan before executing.
- Remote commands built as argument lists, quoted with `shlex.quote`.
- Errors: typed exceptions carrying a human message and a suggested fix, rendered the same in CLI and web.
