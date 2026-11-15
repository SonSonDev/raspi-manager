# Architecture — code map
Updated in the same change as any structural element added, moved or removed. Principles: CLAUDE.md. Order of the steps:
WORKFLOW.md. Dependencies point inward: `cli`/`web`/`gui` → `services` → `core`; `services` → `ssh`, `cloudflare`, `config`.

## Layout (`src/raspi/`)
| Package | Role | Modules |
|---|---|---|
| `core/` | pure logic, no I/O (scripts are built here as text, never run) | `models` (Host, AppRef, EnvFile, status types) · `remote` (status/action/logs/env/manifest scripts and parsers) · `manifest` (render `apps.yaml`) · `env` (parse/render/diff, names only) · `naming` · `scan` (adopt scan) · `provision` (doctor checks, apt whitelist) · `runtime` (nvm pinned + SHA-256) · `deploykey` (key, alias, verify) · `install` (clone + build script) · `unit` (systemd unit text, escaping) · `routes` (hostname/ingress rules) · `connector` (cloudflared install script) |
| `services/` | use cases: core + ssh + cloudflare + config | `status` · `actions` (start/stop/restart, logs) · `inventory` (read Pi `apps.yaml`, publish) · `adopt` (scan, add/update apps) · `declare` (env file declarations) · `env` · `provision` (`doctor`, `provision`) · `runtime` · `deploykey` · `install` · `unit` · `routing` (Cloudflare overview, add/remove route) · `app_route` (route + connector end to end) |
| `ssh/` | `transport.py`: system `ssh` through `subprocess` (`Transport` protocol; scripts sent on stdin to `sh -s`; `CREATE_NO_WINDOW`) | |
| `cloudflare/` | `client` (httpx, token never in errors), `release` (latest cloudflared release) | |
| `config/` | `loader` (hosts + manifest validation), `writer` (hosts.yaml edits, `.bak`), `envstore` (local env copies), `cloudflare_settings` (keyring + ids) | |
| `cli/` | Typer: `main` (status, start/stop/restart, logs, host add/edit/rm/publish/scan/adopt, gui, web), `provision` (host doctor/provision/install-node/deploy-key/install-app/install-unit), `route` | |
| `gui/` | Qt app: `main_window` (table, toolbar, logs dock) + one **mixin per feature** (`*_actions.py`) and its dialogs; `workers.submit` (all SSH off the GUI thread), `fleet_model`, `theme` | actions: host, publish, adopt, provision, deploykey, install, env, route · dialogs: add_host, adopt, add_env, env, install, route, cloudflare |
| `web/` | FastAPI + Jinja (`app.py`, templates `base fleet confirm result logs`), secondary front, token + localhost Host check | |

Tests (`tests/`): one file per feature; services with a fake transport; GUI offscreen (`QT_QPA_PLATFORM=offscreen`).

## GUI rules (they exist because of real bugs)
- A mixin method name must be unique across `MainWindow` and all mixins: a duplicate silently shadows the other (this broke
  “Install app…” once). A test enforces it (`test_install_gui.py`). Mixin helpers get feature-specific names.
- No SSH on the GUI thread: `submit(fn, on_done, on_failed)`; `on_done`/`on_failed` are slots of the window; every failure
  shows a message (no silent path).
- `main_window.py` is at the 400-line limit: new toolbar groups go to their own module before adding more buttons.
- Offscreen captures have no fonts or stylesheet: judge looks on the real platform.

## On the Pi
```
~/.raspi/apps.yaml                         app inventory (names, paths, repo, build, run, env file paths; no secrets)
~/apps/<app>/                              plain git working copy (or the declared path); no releases/ layout
~/.ssh/raspi_deploy_<app>[.pub]            deploy key; alias block `Host github-<app>` in ~/.ssh/config
/etc/systemd/system/<app>.service          app unit, first line "# managed by raspi-manager", EnvironmentFile = declared env files
/etc/systemd/system/raspi-tunnel-<name>.service   cloudflared connector, token in /etc/cloudflared/raspi-tunnel-<name>.env (root, 600)
<env file as declared>                     chmod 600, atomic push, one .raspi-bak
```
Adopted apps keep their own units; only raspi-created ones follow these names.

## Data flows
- **Status**: GUI/CLI → `services.status.collect_host` → per Pi: read `apps.yaml`, then one status script (`systemctl show`,
  `git rev-parse`) → table with “last checked”. Two SSH calls per Pi per refresh.
- **Any change** (WORKFLOW rule 2): `plan_*` (read-only, raises a typed error with a fix) → confirmation showing the exact
  script (`--dry-run` stops here) → `install_*`/`apply_*` → result re-read from the Pi.
- **New app path**: `deploykey` → `install` (clone, build, record in `apps.yaml` via `adopt.add_apps`/`update_app`) →
  `env` push → `unit` → start → `app_route`. Update (planned): fetch → fast-forward → build → restart.
- **Route**: `app_route` → Cloudflare (tunnel, ingress, DNS) → connector script on the Pi (token fetched last, sent over
  stdin, hidden in previews) → verify tunnel health and HTTPS.

## Conventions
- Every mutating service takes `dry_run` or is split into plan and apply; remote commands are argument lists quoted with
  `shlex.quote`, never user text concatenated.
- Errors are typed exceptions with a human message and a suggested fix, rendered the same in CLI and GUI.
- Files < 400 lines (current exceptions to fix: `gui/cloudflare_dialog.py`, `gui/env_dialog.py`, `services/routing.py`).
