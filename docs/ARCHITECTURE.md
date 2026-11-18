# Architecture — code map
Updated in the same change as any structural element added, moved or removed. Principles: CLAUDE.md. Order of the steps:
WORKFLOW.md. Dependencies point inward: `cli`/`web`/`gui` → `services` → `core`; `services` → `ssh`, `cloudflare`, `config`.

## Layout (`src/raspi/`)
| Package | Role | Modules |
|---|---|---|
| `core/` | pure logic, no I/O (scripts are built here as text, never run) | `models` (Host, AppRef, EnvFile, status types) · `remote` (status/action/logs/env/manifest scripts and parsers) · `manifest` (render `apps.yaml`) · `env` (parse/render/diff, template pre-fill; names only) · `naming` · `stage` (next step of an app from its status) · `wizard` (the five setup steps and which are done) · `requires` (prerequisites: which are missing, in words) · `route_health` (per-route answers, “main up, share down”, plan warnings) · `remove` (what can go, the probe, the script) · `move` (probes of both Pis, refusals, tar/checksum/disable scripts) · `deploykey_manage` · `scan` (adopt scan) · `provision` (doctor checks, apt whitelist) · `tools` (ffmpeg and yt-dlp: release files, probe, script) · `runtime` (nvm pinned + SHA-256) · `deploykey` (key, alias, verify) · `deploykey_manage` (list, rotate, delete the keys already on a Pi) · `install` (clone + build script) · `update` (probe, checkout + build script, rollback target) · `unit` (systemd unit text, escaping) · `routes` (hostname/ingress rules) · `connector` (cloudflared install script) |
| `services/` | use cases: core + ssh + cloudflare + config | `status` · `actions` (start/stop/restart, logs) · `inventory` (read Pi `apps.yaml`, publish) · `adopt` (scan, add/update apps) · `declare` (env file declarations) · `env` · `provision` (`doctor`, `provision`) · `tools` (release lookup, plan, install) · `runtime` · `deploykey` (+ `list_keys`, `rotate_key`, `delete_key`) · `install` · `update` (plan, apply, record) · `wizard` (`read_status` of one app) · `remove` (plan, apply: Pi first, list last) · `move` (plan: refusals; apply: stop, copy, verify, start, with rollback) · `unit` · `routing` (Cloudflare overview, add/remove route) · `routing_delete` (guarded tunnel deletion) · `app_route` (route + connector end to end) |
| `ssh/` | `transport.py`: system `ssh` through `subprocess` (`Transport` protocol, plus `ByteTransport.run_bytes` that moves a tar stream through this PC with the script as the remote command and the data on stdin; the script goes on stdin and the Pi reads it whole with `sh -c 'eval "$(cat)"'`, so no command can swallow the rest of it; `CREATE_NO_WINDOW`) | |
| `cloudflare/` | `client` (httpx, token never in errors), `release` (latest cloudflared release) | |
| `config/` | `loader` (hosts + manifest validation), `writer` (hosts.yaml edits, `.bak`), `envstore` (local env copies), `cloudflare_settings` (keyring + ids) | |
| `cli/` | Typer: `main` (+ `update`: host update/rollback · `remove`: host remove-app · `tools`: host install-tools · `move`: host move-app · `deploykeys`: host deploy-keys/deploy-key-rotate/deploy-key-rm) (status, start/stop/restart, logs, host add/edit/rm/publish/scan/adopt, gui, web), `provision` (host doctor/provision/install-node/deploy-key/install-app/install-unit), `route` | |
| `gui/` | Qt app: `main_window` (table, toolbar, logs dock) + one **mixin per feature** (`*_actions.py`) and its dialogs; `workers.submit` (all SSH off the GUI thread), `fleet_model`, `theme` | actions: host, publish, adopt, provision, deploykey, install, update, newapp (`newapp_wizard.py` is the wizard window), remove (`remove_dialog.py`), move, tools (`tools_dialog.py`), env, route; `confirm.py` is the one confirmation every change goes through; `busy.py` refuses a second long change on the same Pi; `icon.py` + `assets/` (SVG source, generated .ico and .png, rebuilt by `scripts/make-icon.py`); `chrome.py` builds the menu bar and the toolbar from shared QActions · dialogs: add_host, adopt, deploykeys (the “Manage deploy keys…” window), add_env, env (+ mixins `env_files` the file picker, `env_push` save/import/push, `env_template` start from or reset to `.env.example`), install, route, cloudflare (+ `cloudflare_routes` mixin with the route and tunnel actions, `add_route_dialog`, `dns_dialog`) |
| `web/` | FastAPI + Jinja (`app.py`, templates `base fleet confirm result logs`), secondary front, token + localhost Host check | |

Tests (`tests/`): one file per feature; services with a fake transport; GUI offscreen (`QT_QPA_PLATFORM=offscreen`); `test_script_syntax.py` runs every generated script through `sh -n` and rejects bashisms (add new script builders to its list).

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
Unit drift (built): the status script also reads our unit's `ExecStart`, `EnvironmentFile`, `WorkingDirectory` (`remote._unit_read_lines`), `core/unit.unit_drift` compares them with the inventory, `core/stage` shows “Unit out of date”; `services/install.plan_install` carries an optional `UnitPlan` (read through `services/unit.plan_unit`), `install_app(with_unit=True)` writes it last; `gui/confirm.confirm_with_option` is the confirmation with a tick box.
Built so far: `core/models.Process` and `AppRef.processes`, validation in `config/loader` (`check_process`), rendering in `core/manifest`, `core/processes.all_processes` (an app without a list is one process, `main`); `core/unit.render_unit(..., process)`, `build_units_script` and a probe with one state per process, used by `services/unit.plan_unit`. Planned (WORKFLOW §6b): everything that acts on them; `processes:` in `apps.yaml` and one `<app>-<name>.service` per process (`core/models`, `manifest`, `unit`,
`stage`; `services/update`, `unit`, `status`; unit drift compared in `core/unit`); not built yet.

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
- Files < 400 lines, tests included (checked at each checkpoint; the three long files were split on 2026-11-15).
