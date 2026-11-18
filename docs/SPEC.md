# Product specification
Source of truth for scope and behaviour. The *order* of the steps lives in `WORKFLOW.md`. Status: `done` · `partial` ·
`planned` · `dropped`. “Fake Pi” = tested only with a simulated Pi/Cloudflare; the first real run is up to the developer
(see the real-Pi log in WORKFLOW.md).

## Overview
`raspi-manager` is a native Windows app (Qt), a CLI and a secondary local web dashboard (`raspi web`, `127.0.0.1`) that
manage apps on several Raspberry Pi over SSH. Config lives in `~/.raspi/` on the PC; each Pi keeps its own app inventory
(`~/.raspi/apps.yaml`). Real state is always read from the Pi.

**GUI parity rule:** everything needed to bring a project live is doable from the GUI without typing commands; the CLI is
the scriptable twin and both call the same services.

## Out of scope
Agent on the Pi, dashboard auth or remote exposure, sops/encrypted secrets, Docker, webhook/CI auto-deploy, metrics
history, mobile, Cloudflare Access/WAF, multi-Pi load balancing.

## Interfaces
- CLI: `raspi <group> <verb>`; every mutating command has `--dry-run`; destructive ones ask (`--yes`).
- GUI: one table (Pis × apps: status, version, last checked), a toolbar of actions, a logs dock. See DESIGN.md.

## Status at a glance
| Feature | Status |
|---|---|
| F1 Pis and provisioning | partial: add/edit/remove Pi, doctor, provision, Node, deploy key; uv, `role`, key wizard not done |
| F2 App inventory | partial: Pi inventory, publish, adopt scan, declare env files, install app, install unit; remove/edit fields, `built`, `requires` not done |
| F3 Update and rollback | done (fake Pi) |
| F4 Lifecycle and logs | partial |
| F5 Environment variables | partial (GUI done, CLI `env` not done) |
| F6 Cloudflare routing | partial (fake Cloudflare only) |
| F7 Main UI | partial |
| F7b Guided “New app” wizard | done (fake Pi) |
| F8 Fleet health | planned |

## F1 — Pis and provisioning
- **Add / edit / remove a Pi** (done): GUI “Add a Pi…” (Ctrl+N), “Edit Pi…” (Ctrl+Shift+E; address and user, name locked),
  “Remove Pi…” (lists the apps forgotten; only `hosts.yaml` changes, `.bak` kept); CLI `raspi host add|ls|edit|rm`.
  Values are validated like the loader. No hosts file (or an empty one) shows a how-to panel with the SSH key steps and an
  “Open setup guide” button (`docs/PI-SETUP.md`). Nothing is sent to the Pi: key access and host-key trust must already work.
- **Check** (done, fake Pi): “Check Pi…” / `host doctor NAME`, read-only: system, passwordless sudo, git, curl, uv, Node
  (also under nvm), cloudflared, `~/apps`, free disk (≥ 1 GB), memory; each gap says how to fix it.
- **Provision** (done, fake Pi): “Install missing…” / `host provision NAME [--dry-run] [--yes]` installs only what is
  missing and installable: `git`, `curl` through apt (fixed whitelist) and `~/apps`, after a confirmation showing the script.
- **Node** (done, fake Pi): “Install Node…” (in the Check window) / `host install-node NAME [MAJOR=22]`: nvm pinned to
  tag v0.41.0, installer SHA-256 checked on the Pi, no sudo (nvm edits the shell profile), then `nvm install MAJOR` as the
  default. Lines offered: 22, 24, 26.
- **Deploy key** (done, real Pi verified once): “Deploy key…” / `host deploy-key NAME APP [--repo OWNER/NAME]` creates
  `~/.ssh/raspi_deploy_<app>` (ed25519, no passphrase) and the alias `github-<app>` in `~/.ssh/config`, adds GitHub's host
  keys to `known_hosts` when github.com is absent (read by the PC from `api.github.com/meta` over HTTPS), shows the public
  key to add as a read-only deploy key, and with `--repo` checks it with `git ls-remote` (“Check again”). Existing keys and
  settings are kept.
- **Manage deploy keys** (done, fake Pi; list and delete scripts also run for real in a throw-away home): **Pi → Manage deploy keys…** / `host deploy-keys NAME [--check]`, `host deploy-key-rotate NAME APP`, `host deploy-key-rm NAME APP` (both `--dry-run`, `--yes`). Lists, read-only, the keys of `~/.ssh/raspi_deploy_*` and the alias blocks carrying our marker: app, repository (from the app of the same name in the inventory), fingerprint, used or not, “incomplete” when only one of key file and alias exists. “Check access” (`--check`) runs `git ls-remote` per key and shows ✓ accepted / ✕ refused. “Copy public key”. **Rotate** deletes the key and makes a new one (alias and GitHub host keys kept or restored) and shows the new public key to add on GitHub; **Delete** removes the key files and the alias block only. Both show the exact script, warn when an app still uses the key, and are refused while another change runs on that Pi. Not done: adding or deleting the key on GitHub (needs a GitHub token; stays manual), keys not made by raspi-manager.
- **Tools** (done, fake Pi; the read-only plan rehearsed on `raspi` and `raspi-v`): **Pi → Install tools…** / `host install-tools NAME [ffmpeg] [yt-dlp] [--dry-run] [--yes]`. A picker shows what the Pi has and ticks what is missing or old. ffmpeg comes from the distribution through apt. yt-dlp comes from the latest official GitHub release (Debian's package is months old): the file for the Pi's architecture (`yt-dlp_linux` x86_64, `yt-dlp_linux_aarch64` a single executable, `yt-dlp_linux_armv7l.zip` a zipped folder unpacked in `/opt/yt-dlp`), its SHA-256 from the release's `SHA2-256SUMS`, run once (`--version`) before anything is replaced, installed as `/usr/local/bin/yt-dlp` (root, 755; in the PATH of raspi's units); installing again updates it. Needs passwordless sudo and curl; the 32-bit bundle also needs unzip (installed through apt). “Check Pi…” lists both tools as optional. The release signature (`.sig`) is not verified, only the checksum.
- Not done: installing uv (method open), `role`, a key-install wizard (the SSH key is a terminal step).

## F2 — App inventory and install
- **Inventory on the Pi** (done): `~/.raspi/apps.yaml` (`version: 1`, then `apps:`) is the source of truth; fields: `name`,
  `unit`, `path`, `env_files` (label + path), `repo` (owner/name), `build`, `run` (one-line commands, ≤ 500 characters,
  validated, shown in full before anything runs). `built: <rev>` (done: set after a successful build when a build is declared). `previous: <rev>` (done: the revision built before the last update) and `requires:` (done: prerequisites, see below). For an app with a `repo`, the status also reads its working copy (`~/apps/<app>` when no `path`), whether its deploy key exists and whether each declared env file exists.
  Read on every refresh; a missing file falls back to the apps in `hosts.yaml`; an invalid file is ignored with the reason
  in the status bar. Every write: applied on the file read just now, atomic (temp + mv, mode 600), `.bak` kept, read back.
- **Publish** (done): “Publish apps to Pi…” / `host publish NAME` writes the `hosts.yaml` apps to a Pi that has no
  `apps.yaml` (never replaces an existing one).
- **Adopt scan** (done): “Scan apps…” / `host scan NAME` lists, read-only, services in `/etc/systemd/system` run by the login
  user and not yet in the inventory (state, git folder, command; read with `systemctl show`); “Add selected…” /
  `host adopt NAME APP...` adds the ticked ones. Services are not touched. Not done: other sources (ports, cloudflared
  config, nvm/pyenv).
- **Install app** (done, fake Pi + real read-only probe): “Install app…” / `host install-app NAME APP --repo OWNER/NAME
  [--build CMD] [--run CMD] [--path DIR]`. Read-only probe (deploy key present, ≥ 500 MB free, folder state), exact script
  shown, then `git clone` through the key alias into `~/apps/<app>` (or `--path`), the build command with nvm's default Node
  (no sudo), and the entry added to the Pi's `apps.yaml` (unit `<app>.service`). An existing git working copy is kept as is;
  a non-git folder is refused. Run again for a known app, it updates the entry (env files kept) and rebuilds: the GUI form
  opens pre-filled as “Update an app”. The run command is required in the GUI.
- **Prerequisites** (done, fake Pi): `requires:` entries `{kind: folder|file|mount, path: /absolute}` (no `..`, no duplicates), typed one per line in the install form or with `install-app --requires mount:/mnt/data` (repeatable). Checked read-only on every status and reported by name, never created: a missing one shows “Prerequisite missing” in Next step, is listed by the wizard's start step, and makes Start ask “Start anyway?”. They do not stop a start.
- **Install unit** (done, fake Pi): “Install unit…” / `host install-unit NAME APP`: writes and enables (does not start)
  `/etc/systemd/system/<app>.service` marked `# managed by raspi-manager`: `run` through `/bin/sh -c "exec …"`, login user,
  working copy, `Restart=on-failure`, nvm's Node first in `PATH`, one `EnvironmentFile=` per declared env file. A unit
  without our marker is never overwritten. Needs passwordless sudo; reports a missing Node or env file.
- **Remove app** (done, fake Pi; the script also run for real in a throw-away home): “Remove app…” / `host remove-app`. Six items, each separate: the list entry in `apps.yaml` and the unit (stopped, disabled, file deleted; only if made by raspi-manager) are ticked at first; the working copy (git working copies under the home folder only, and not when they hold a `requires:` path), the deploy key and its ssh alias, the env file(s) on the Pi (under home) and the local env copies on the PC are unticked. Unavailable items are greyed with the reason. The exact script is shown and follows the ticks; the app name must be typed (CLI: prompted, `--yes` skips). Pi first, list entry last. Never touched: `requires:` folders, anything outside home, the Cloudflare route and tunnel.
- **Move to another device** (done, fake Pi; scripts also run for real in a throw-away home): **App → Move to another device…** /
  `host move-app SOURCE APP TARGET [--dry-run] [--yes]` carries an app's data folders (`requires: folder`) and env files to a
  Pi where the app is already set up at the same revision, then starts it there and leaves the source stopped and disabled,
  its data kept. Refusals, checksums and rollback: WORKFLOW §6 item 7a. Needs `ssh` to both Pis from this PC (data is relayed
  through it, never Pi to Pi). Not done: app-provided check command, route and inventory follow-up.
- **Several processes per app** (planned, decided 2026-11-18; flow in WORKFLOW §6b): `processes:` list `{name, run,
  on_update}` in `apps.yaml`, one unit `<app>-<name>.service` each; `on_update`: `restart` (default), `keep` (an update or
  rollback never restarts it), `ask` (tick box in the Update confirmation); one table row per process; “old code” when a
  kept process predates the last build; an existing `<app>.service` is adopted as `main`. A single `run` stays valid.
- **Unit drift** (detection done, fake Pi; the read-only lines also run in a throw-away shell): a unit of ours whose run
  command, environment files or working folder differ from `apps.yaml` shows “Unit out of date (Install unit…)” in Next
  step, with the field names in the tooltip (never values). “Install app…” on a known app whose `run` or `path` changed, with a unit of ours installed, adds a ticked box “Also rewrite the unit …” (CLI `--update-unit/--keep-unit`); the unit is written after the inventory and the app is never restarted. Not offered for a new app, a missing unit (use “Install unit…”) or a foreign one. Writing a unit never restarts the process.
- Not done: edit fields other than through Install app.

## F3 — Update and rollback
Status: `done` with a fake Pi (the plan's read-only part rehearsed on `raspi-v`).
- “Update…” / `host update NAME APP [--ref REF] [--dry-run] [--yes] [--no-restart]`: the plan fetches from GitHub, resolves the
  ref (default: latest of the default branch; a branch, a tag or a commit), and shows old → new revision, the incoming
  commit subjects, the steps and notes; refused for an app without `repo`, not cloned, with uncommitted changes to tracked
  files, or with commits that exist only on the Pi. Applying checks out that exact commit (detached), runs the build with
  nvm's Node, records `built` and `previous` in `apps.yaml`, and restarts the unit only if it was running (and sudo works).
- Rollback: “Update…” → “Go back to the previous version” / `host rollback NAME APP`: same mechanism to `previous`, or to
  the last `built` revision after a failed update; `previous` is cleared.
- A failed build leaves the working copy on the new commit, restarts nothing, records nothing; the “Next step” column then
  says “Build” and the error message points to the rollback.
- Not done: listing available tags/branches in a picker, an “update available” hint in the table, keeping several releases.

## F4 — Lifecycle and logs
- Done: `status` (state glyph + word, git rev with `*` when dirty, since, restarts, last checked, unreachable shown as an
  error); `start|stop|restart` through `sudo -n systemctl` (CLI: stop/restart ask unless `--yes`; all have `--dry-run`; GUI:
  stop/restart confirm, start is direct); `logs` (read-only, ≤ 1000 lines); 10 s auto-refresh, per-host jobs.
- Dashboard mutations need a per-process token and a localhost Host header.
- Done: an app whose unit does not exist shows “– no unit”; Start is disabled with a tooltip pointing to “Install unit…”, and a failed start on a missing unit says the same.
- Planned: `pause`/`resume`
  (SIGSTOP/SIGCONT, state `paused`); follow mode for logs.
- Single mode: system units run as the login user with passwordless sudo. Adopted apps keep their own unit names; units
  created by raspi are named `<app>.service`.

## F5 — Environment variables
- GUI done (“Environment…”, Ctrl+E), fake Pi: editable table with masked values and a “Show values” toggle; import from the Pi
  (explicit click); save locally; push; push and restart; confirmation lists variable **names** only; sync line (“same as
  the Pi” or +/~/- counts). Several files per app (`env_files: [{label, path}]`): a picker, one local copy
  (`<host>.<app>.<label>.env`) and one sync state each. Push is atomic, mode 600, keeps one `<file>.raspi-bak`, content sent
  as base64.
- Declaring files from the GUI (done): with none declared, “Environment…” lists `.env*` candidates found read-only up to two
  folders below the app (templates and our backups skipped) and writes the declaration to the Pi's `apps.yaml` (or
  `hosts.yaml` when the Pi has none).
- Done: templates (`.env.example`, `.sample`, `.template`, `.dist`) are found next to the env files, also for an app without a declared path (`~/apps/<app>`); a template with no `.env` beside it proposes creating one, and the editor then starts from it (project defaults kept, secret-looking names and placeholders empty); empty variables are counted in the sync line and named in the push confirmation. Planned: the same pre-fill inside the “New app” wizard.
- **Reset from template…** (done): in the editor, replaces the variables by the template's after a confirmation; nothing pushed.
- Not done: CLI `raspi env ...`, scan of code for missing `process.env.X`, required-keys check before deploy, per-Pi overrides.
- Values are secrets: never printed, logged, in tooltips, exceptions or test messages.

## F6 — Cloudflare routing
Built and tested against a simulated Cloudflare only (never run on the real account).
- **Cloudflare window** (“Cloudflare…”, Ctrl+Shift+C): API token in the Windows Credential Manager (never shown or in a
  file), zone name/ID and account ID looked up from the token and saved in `~/.raspi/cloudflare.yaml`; table of all tunnels
  with routes and status; “Add route…” (Pi + optional project + hostname + local `http://localhost:PORT`; tunnel
  `raspi-manager-<pi>[--<project>]`), “Edit route…”, “Remove route” (ingress rule only, DNS left), “Delete tunnel…” (only
  raspi-created tunnels, refused while healthy/degraded, typed-name confirmation), “DNS records…” (read-only list).
- **Add route** computes a read-only plan first (create tunnel, ingress rule with the catch-all `http_status:404` last,
  proxied CNAME) and asks to confirm. A hostname with a single CNAME can be **repointed** (PATCH, with a warning when the old
  tunnel is healthy); A/AAAA/TXT or several records are never touched; other conflicts are refused, never overwritten.
- **A running connector is trusted only when its tunnel is connected** (2026-11-16): systemd keeps a `cloudflared` unit “active” while it retries forever with a token whose tunnel was deleted (“Unauthorized: Tunnel not found”). So when the unit runs but the tunnel has to be created, or Cloudflare reports it as anything but healthy/degraded, Set up route… replaces the token and restarts the unit (the confirmation says why); a Cloudflare lookup failure keeps what runs.
- **Set up route** (done): “Set up route…” / `raspi route add HOSTNAME APP[:PORT] [--host] [--repoint]`: port read from the
  app's listening sockets (editable), tunnel `raspi-manager-<pi>--<app>`, one confirmation listing the Cloudflare changes
  and the exact Pi script (token hidden), then the Pi side only if its unit is not already running: official `cloudflared`
  for the Pi's architecture (SHA-256 from the release notes), token in `/etc/cloudflared/raspi-tunnel-<name>.env` (root,
  600), `raspi-tunnel-<name>.service`; then wait for healthy and ask the public URL once. The token travels over ssh stdin and
  is never shown or saved on the PC.
- Cloudflare API facts (official docs, checked 2026-10-17): `POST /accounts/{id}/cfd_tunnel` `{"name","config_src":"cloudflare"}`
  returns `id` and `token`; `PUT …/cfd_tunnel/{tunnel}/configurations` with `config.ingress`; `POST /zones/{zone}/dns_records`
  proxied CNAME to `<tunnel-id>.cfargotunnel.com`; token needs Account › Cloudflare Tunnel (Edit) and Zone › DNS (Edit).
- **Several routes per app** (done, fake Pi/Cloudflare): `routes:` in the app's inventory entry (`hostname`, `port`, `check` path); Set up route… / `route add [--check PATH]` is run once per hostname and records each success; plan warnings for a hostname or port shared with another app. Every status reads each route locally (status code only); a route that does not answer shows “Route not answering” in Next step with “host :port up/down” detail.
- Not done: deleting DNS records, removing a route from the inventory when it is removed in Cloudflare, removing a connector from a Pi,
  per-app routes in the main table.

## F7 — Main UI
Native Qt app (`raspi gui`, platform look): menu bar (Pi, App in workflow order, Cloudflare) + one slim toolbar (Restart, Start, Stop, Logs | Environment, Update | Refresh), see DESIGN.md; live table, Restart/Start/Stop/Logs/Refresh with shortcuts (F5, Ctrl+R, Ctrl+S, Ctrl+L),
confirmation dialogs, logs dock, unreachable and “checking” states, light/dark. The web dashboard is a secondary front.
Done: a **“Next step”** column per app (WORKFLOW §2: deploy key, clone, build, run command, environment, unit, start, ready) with the reason in the tooltip. Planned: the other buttons following the stage; “routed” in the column; version picker; tray icon. Every screen has loading, empty, error
and success states.

## F7b — Guided “New app” wizard
Status: `done` with a fake Pi (the step states were read for real, read-only, on `raspi-v`). **App → New app…** (Ctrl+Shift+N): one form (name, repository, build, run, folder; run required), then a window listing five steps with a glyph (✓ done, ▶ current, ○ waiting) and a detail each: deploy key (created and checked with GitHub; a key GitHub refuses shows the public key to add), clone and build, environment (the existing editor, pre-filled from `.env.example`; or “the app needs no environment file”, remembered for the session only), systemd unit, start. Each step asks for confirmation with the exact script. The state is re-read from the Pi before and after every step, so closing the window at any point is safe: **App → Continue setup…** on the selected app reopens it at the first step not done. At the end it points to Cloudflare → Set up route….
Not done: an “Add a Pi” key-install wizard, pre-flight checks.

## Project compatibility checklist (what an app needs to be managed by raspi)
1. A git repo with a remote (deploy by ref). 2. A declared Node/Python version (`engines`, `.nvmrc`). 3. A build step as a
script when needed. 4. A single run command, no absolute `/home/...` paths. 5. Configuration through environment variables,
`.env` not committed (an `.env.example` helps the wizard). 6. Persistent data in a dedicated folder outside the working
copy (declare it in `requires:`). 7. No untracked secrets in the working tree. 8. A clean working tree (updates are refused
otherwise).

## F8 — Fleet health and extras
Status: `planned`: Pi temperature, disk, memory; grouped `apt upgrade`; env backup. Not before F1–F7 work.

## Reference apps
- **notes-app** (Node 26 + Svelte PWA, first real app on `raspi-v`): `npm ci && npm run build`, `npm start`, env through
  `EnvironmentFile`, a data directory with a marker file on an external disk (`DATA_DIR`), exposed through a tunnel.
- **web-game** (Discord game, to move to `raspi-v`): Node 22, `ffmpeg` (apt), `yt-dlp` (standalone binary or pipx, since
  PEP 668 forbids `pip --user` on trixie), two `.env` files (client and server), port 2567, Discord Developer Portal URL
  mapping (manual).

## Open questions
See QUESTIONS.md.
