# Decision log
One entry per decision, newest first. A decision is only changed by a new entry that supersedes it.
Not logged: visual tweaks, tuning values, reverted trials.

## 2026-11-15 — Clone and build: plain working copy in `~/apps/<app>`, existing copy untouched, app recorded after a successful build
- Decision: `install-app` clones into `~/apps/<app>` (or the app's `path`) with a plain `git clone` of the default branch through the deploy-key alias, runs `build` in it, and only then adds the entry to the Pi's `apps.yaml` (unit name `<app>.service`). A folder that is already a git working copy is kept as is (no fetch, pull or checkout); a non-empty folder that is not one is refused. `build` and `run` are one-line shell commands from the developer's own `apps.yaml`: validated (no control characters, 500 characters), shown in full before anything runs, never assembled from other values.
- Why: the `releases/<sha>` layout and version switching belong to the deploy step (F3), so install stays a safe first step that can be repeated; recording after the build means a failed build leaves no half-declared app and the command can simply be run again. Notes App (first real app) is built with `npm ci && npm run build` and started with `npm start`, with its environment from the unit's `EnvironmentFile`.
- Alternatives rejected: the `releases/<sha>` layout now (it is F3 and needs the unit and env work first); recording the app before building (a failed build leaves a unit-less entry); pulling an existing copy (could discard or clash with local edits).
- Consequences: no unit exists yet after install (next slice generates it from `run`, the env file and the absolute `node` path); the Pi's Node must already be installed (`install-node`) for apps that need it; the build runs with the Pi's user privileges, so only commands the developer wrote should go in `build`.

## 2026-11-15 — An app's repo, build and run commands are declared in the Pi's `apps.yaml`
- Decision: the app entry in `~/.raspi/apps.yaml` gets the optional fields `repo` (owner/name), `build` (commands run after checkout) and `run` (command the generated unit starts), later `port`. Clone and build (F10 steps 5-6) and unit generation (step 8) read them from there. Not built yet: schema, validation, wizard.
- Why: follows the 2026-11-14 decision that the Pi is the source of truth, so a reinstalled PC finds how each app is installed; no change needed in the projects themselves.
- Alternatives rejected: a `raspi.yaml` in each repo (every project to edit, and raspi must clone before it knows what to do); typing the commands at each install (nothing to replay on redeploy).
- Consequences: `parse_manifest`, `render_manifest` and `AppRef` grow these fields; the values are commands, so they are validated and shown in full before anything runs, never built by string concatenation.

## 2026-11-15 — Deploy keys: one read-only ed25519 key and one ssh alias per app, GitHub host keys from its API
- Decision: each app gets `~/.ssh/raspi_deploy_<app>` (ed25519, no passphrase, comment `raspi-<app>@<pi>`) created on the Pi, and a `Host github-<app>` block in `~/.ssh/config` (`HostName github.com`, `User git`, `IdentitiesOnly yes`, guarded by a marker comment so it is added once). The repo is cloned through that alias (`git@github-<app>:owner/name.git`). GitHub's host keys are put in `known_hosts` only when `github.com` is absent, taken from `https://api.github.com/meta` (`ssh_keys`, fetched by the PC over HTTPS, format-checked). The public key is shown for the developer to add as a read-only deploy key; `git ls-remote` (BatchMode, no prompt) verifies it. Existing keys and settings are never replaced.
- Why: a deploy key is tied to one repo, so one alias per app lets each repo use only its own key; no passphrase because deploys must run unattended; trusting GitHub's published keys avoids accepting a host key blindly on first use.
- Alternatives rejected: one shared key (GitHub refuses the same deploy key on two repos); `ssh-keyscan` (trust on first use); a key generated on the PC and copied (private key would travel); adding the key through the GitHub API (needs a token with admin rights on the repo, not wanted).
- Consequences: the key has no passphrase, so anyone with the Pi's login can read that repo (read-only); removing a key means deleting the two files and the config block by hand; not built: rotating a key.

## 2026-11-15 — Provisioning starts with `doctor` and a minimal `provision` (apt git/curl + `~/apps`)
- Decision: installs are triggered from raspi-manager in small slices, first the base of every Pi. `doctor` (read-only) lists the gaps; `provision` installs only `git` and `curl` through apt (a fixed whitelist, never a name from outside) and creates `~/apps`, after a confirmation showing the exact script. Node and uv are reported by `doctor` as optional gaps and not installed yet.
- Why: apt packages come from the distribution with its own signature checks, so this slice adds no third-party download. Node (nvm vs NodeSource vs the trixie package) and uv (installer script vs release binary) each need a method and a version policy, which is the developer's call.
- Node method chosen 2026-11-15: **nvm**, with the Node version picked per Pi or app (as on `raspi` today; units then use the absolute `node` path). Built the same day: nvm pinned to v0.41.0 (latest stable on GitHub, GPG-signed tag, checked online 2026-11-15); its install.sh is fetched from that tag and its SHA-256 (045932fe…f48b, computed from the tag's file) is checked on the Pi before it runs; offered Node lines 22, 24, 26 (LTS on nodejs.org the same day). The script runs without sudo and lets nvm edit the shell profile. Re-check the nvm tag and the LTS lines when touching this. uv: method still open.
- Alternatives rejected: the whole F10 wizard at once (too large to review); installing uv/Node with `curl | sh` without deciding (unpinned remote script run with the Pi's user).
- Consequences: a Pi without Node or uv is “ready” for apps that do not need them; the next slices are Node/uv, then the deploy key, clone and build, unit generation (F10 steps 4-8).

## 2026-11-14 — The Pi holds the app inventory (`~/.raspi/apps.yaml` on each Pi); the Pi is the source of truth
- Principle (reworded): the Pi describes and runs its apps; the PC is a control station that keeps only the address book (name, address, user in `hosts.yaml`) and credentials (SSH key, Cloudflare token in the Credential Manager). After a PC reinstall, re-adding the Pis must be enough to find the management state as it was left.
- Decision: each Pi carries `~/.raspi/apps.yaml` (user-writable, no sudo): `version: 1`, then per app name, systemd unit, repo folder, env files as label + path (never values), later port, deployed version, hostname. raspi-manager reads it from the Pi on every refresh, like live state; a missing file falls back to the apps in the PC `hosts.yaml`; an invalid file is ignored, shown in the status bar, and the fallback is used. Writes to the Pi file (add app, declare env file, publish) are atomic with a `.bak` on the Pi, previewed, confirmed, and run by the developer from the GUI. The `.env` file on the Pi is the reference for its variables; the local copy in `~/.raspi/env/` is a working draft.
- Clarification: Cloudflare is not on the Pi. Tunnels and routes live at Cloudflare and are read through the API each time; `apps.yaml` may name an app's public hostname but is not the truth for route state.
- Why: a reinstalled PC without `~/.raspi/` must not lose the management state; also resolves the BRIEF risk “`.env` files exist only on the PC”.
- Alternatives rejected: PC file stays the truth with a pushed backup copy (two sources to keep aligned, stale-copy risk).
- Consequences: not recovered by the Pi file alone: the list of Pis itself, the SSH key and host-key trust, the Cloudflare token and `~/.raspi/cloudflare.yaml` (ids can be looked up again from the token), local env drafts (re-importable from the Pi). Built so far: reading and validating `apps.yaml`, fallback, status bar note (slice 1); publishing the PC's apps to a Pi that has no `apps.yaml`, GUI and CLI, preview + confirmation + atomic write + `.bak` + read-back (slice 2; an existing Pi file is never replaced from the PC). Env file declarations are written where the app list lives: the Pi's apps.yaml when it has one (applied on the file read at that moment, atomic, `.bak`, read-back), hosts.yaml otherwise (slice 3). Whole apps are added by the adopt scan (read-only detection of the login user's services under /etc/systemd/system, explicit ticking, confirmed write; slice 4). Not built: removing a whole app or editing its fields, scan sources beyond systemd units. Until slice 3, “Declare an environment file” still edits the PC `hosts.yaml`, so it does not work for an app that only exists in a Pi's `apps.yaml`. `/etc/systemd/system/*.service` are not readable without sudo on `raspi`, so a scan reads units with `systemctl show`.

## 2026-11-14 — App routes: one connector unit per tunnel, token in a root-only env file
- Decision: “Set up route…” / `raspi route add` give each app its own tunnel (`raspi-manager-<pi>--<app>`) and install its connector on the Pi as `raspi-tunnel-<name>.service` (`ExecStart=<cloudflared> --no-autoupdate tunnel run`, `Type=simple`, `Restart=on-failure`) reading `TUNNEL_TOKEN` from `/etc/cloudflared/raspi-tunnel-<name>.env` (root, mode 600). `cloudflared` is installed only when missing, from the official GitHub release for `uname -m` (`arm`, `armhf`, `arm64`, `amd64`), after checking the SHA-256 published in the release notes; an existing install is reused as is. The plan is read-only, one confirmation shows every change and the exact Pi script with the token hidden; nothing runs on the Pi if its unit is already active. Verified online 2026-11-14: latest release 2026.11.0 (12 Nov 2026) with those four file names, checksums only in the release notes (`### SHA256 Checksums:`), token accepted by `tunnel run` through `--token` or `TUNNEL_TOKEN`, `--protocol auto` falls back from QUIC to HTTP/2 when UDP is blocked.
- Why: the official `cloudflared service install <token>` owns a single `cloudflared.service`, which cannot serve one tunnel per app on the same Pi, and it puts the token in the unit file where `systemctl status` and `ps` show it; the old `cloudflared.service` on `raspi` was in a restart loop for weeks (114k restarts, start timeout 15 s), so the new unit has no short start timeout.
- Alternatives rejected: `cloudflared service install` (single service, token visible); showing commands to paste in a terminal (token on screen and in shell history, chosen against by the developer); an apt install (needs repository setup, no checksum step of ours); a shared tunnel per Pi (the developer's per-project layout already exists in Add route…).
- Consequences: a Pi can run several tunnels; removing a connector is not built (disable and delete the unit and env file by hand); the token is readable only by root on the Pi and travels over ssh stdin; the checksum protects against a corrupted or truncated download, not against a compromised GitHub release since both come from GitHub. The port detection only sees processes of the login user (`ss -p`).

## 2026-11-14 — Pi CRUD put back in the GUI and CLI (reverses the removal logged just below)
- Decision: “Add a Pi…”, “Edit Pi…”, “Remove Pi…” and `raspi host add|edit|rm` are back, as specified in the two entries further down. Kept from the removal episode: the first-run how-to panel (shown with no hosts file or an empty one: SSH steps, “Open setup guide” button) and the rule that an empty hosts.yaml means no Pi.
- Why: the developer asked to restore it right after the removal.
- Alternatives rejected: none discussed; in-app host-key trust (`ssh-keyscan` + fingerprint confirmation) was proposed and not chosen, the first connection stays a terminal step (`docs/PI-SETUP.md`).
- Consequences: the removal entry below is superseded. The dialog file `gui/add_host_dialog.py` was rewritten from its tests, since the original was untracked in git and lost; behaviour is covered by `tests/test_add_host.py`.

## 2026-11-14 — Pis are registered by hand; the Pi CRUD is removed from the GUI and CLI
- Decision: removed “Add a Pi…”, “Edit Pi…”, “Remove Pi…” (GUI) and `raspi host add|edit|rm` (CLI), with their dialog, writer functions and tests. A Pi is added by editing `~/.raspi/hosts.yaml`; `docs/PI-SETUP.md` documents the terminal commands (ssh key, first-connection host key check with fingerprint comparison, file format). `raspi host ls` stays.
- Why: the developer asked for terminal instructions instead of in-app Pi management; a Pi needs SSH and host-key trust set up outside the app anyway, and the app cannot safely accept host keys for the user.
- Alternatives rejected: in-app host-key trust (`ssh-keyscan` + fingerprint confirmation, on “Add a Pi” or on error); keeping the CLI twin for scripting.
- Consequences: supersedes the two 2026-11-14 entries below (first-run “Add a Pi”, edit/remove) and weakens the GUI parity rule for Pis (F7b wizard still planned, to be reconsidered). The first-run state stays: a how-to panel in the window (commands, YAML example, “Open setup guide” button) shown when there is no hosts file or it is empty; real config errors still use the red banner. Possible next step, not decided: a read-only scan of a Pi's apps and a manifest file on the Pi.

## 2026-11-14 — Pi edit/remove: name is locked, removal forgets its apps after confirmation
- Decision: editing a Pi changes only address and user; the name is its identity. Removing a Pi deletes its block (with its declared apps and env files) from hosts.yaml after a confirmation that lists the apps (CLI: prompt unless `--yes`). Nothing is sent to the Pi; local env copies and Cloudflare tunnels are left alone.
- Why: local env copies are named `<host>.<app>.env` and tunnels `raspi-manager-<pi>`, so a rename would orphan them silently.
- Alternatives rejected: renaming with automatic migration of copies and tunnels (large, risky, not needed yet); refusing to remove a Pi that has apps (forces hand-editing YAML, against GUI parity).
- Consequences: to rename, remove and re-add (the env copies must be renamed by hand); leftover local env copies and tunnels of a removed Pi stay until cleaned by hand.

## 2026-11-14 — First run: hosts.yaml is created by “Add a Pi…” / `raspi host add`
- Decision: a missing `hosts.yaml` is a first-run state (`HostsFileMissing`), not an error: the GUI opens with a hint banner and an “Add a Pi…” button; the dialog and the CLI twin only collect name/address/user and write the file through the existing validated writer. No SSH, no key install, no apps yet.
- Why: the developer hit “hosts.yaml not found” at launch and had to hand-write YAML, against the GUI parity rule; smallest slice that unblocks the first launch.
- Alternatives rejected: shipping a template file (still hand-edited); doing the full “Add a Pi” wizard with key install now (needs a password-handling design, kept for F7b).
- Consequences: apps still have to be added by hand in hosts.yaml until the adopt scan exists; the new Pi shows “unreachable” until key access works.

## 2026-11-14 — Main UI stays Qt; the CLI is completed instead of building a TUI
- Decision: the native Qt app remains the main interface. No terminal UI (TUI) for now; the next interface work is the missing CLI twins (`raspi env ...`, `raspi route ...`) calling the same `services/`.
- Why: the env editor, Cloudflare window and confirmation flows already exist in Qt and are dense forms/tables that fit a terminal poorly; a TUI would be a rewrite plus a new dependency (e.g. Textual) needing approval.
- Alternatives rejected: a Textual fleet-view TUI alongside Qt (possible later if the need persists); full migration to a TUI (large rewrite, would supersede the GUI parity rule and DESIGN.md).
- Consequences: GUI parity rule unchanged; CLI gaps listed in SPEC F5/F6 become the next scope.

## 2026-10-17 — Env files are declared from the app; hosts.yaml is rewritten by raspi
- Decision: detection is read-only (`find` for `.env*`, 2 levels, skipping node_modules/.git/dist and templates/backups) and only proposes; the user confirms the label and path; raspi then edits `~/.raspi/hosts.yaml` through `yaml.safe_dump` with a `.bak` copy and validation before writing.
- Why: the developer asked to declare env files from the app instead of editing YAML; detection found a file we had missed (`web-game/client/.env`).
- Alternatives rejected: fully automatic declaration (the app cannot know which file a project really loads, e.g. web-game forces `server/.env` in code); a separate settings file (two sources of truth).
- Consequences: comments in hosts.yaml are lost on the first edit (backup kept); detection says where `.env*` files are, not which one the code loads.

## 2026-10-17 — An app can have several env files (`env_files` with labels)
- Decision: `AppRef.env_files` is a list of `(label, path)`; `env_file: path` stays as a shortcut for one file labelled `default`. `web-game` has `server` (`server/.env`, read by the Node server) and `client` (root `.env`, `VITE_CLIENT_ID`, apparently for the Vite client build). Local copy file names: `<host>.<app>.env` for `default` (unchanged), `<host>.<app>.<label>.env` otherwise. Late read results for a file the user already switched away from are ignored.
- Why: the developer asked to manage the root `.env` of `web-game` too; the two files have different readers and must not be mixed up.
- Consequences: "Push and restart" restarts the service whatever the file; for build-time files (client) a restart does nothing useful, the client must be rebuilt (not handled yet).

## 2026-10-17 — Environment editing: per-app `env_file`, PC copy + push, values never logged
- Decision: each app declares the env file it reads (`env_file` in `hosts.yaml`, user-writable path only). The GUI edits a local copy (`~/.raspi/env/<host>.<app>.env`, outside git), compares it with the Pi file read on open, and pushes it atomically (temp file in the same folder, `umask 077`, `chmod 600`, `mv`, one `.raspi-bak` backup). Content travels base64-encoded on ssh stdin. Values are masked by default; messages, tooltips, labels and confirmations only ever contain variable names or counts.
- Why: apps today read a `.env` in their folder (dotenv), so writing that file needs no unit change and no sudo; keeps adoption non-breaking. Format kept simple (raw when safe, double-quoted otherwise) so both dotenv and systemd read it the same way; ` #` inside unquoted values is flagged because dotenv and systemd disagree on it.
- Alternatives rejected: `EnvironmentFile=` in raspi-generated units now (needs unit regeneration, planned later); sops/encrypted store (out of scope in T1); editing through sudo/system paths (rejected paths: /etc, /usr, /var...).
- Consequences: the `.raspi-bak` files hold the previous secrets (mode 600); the local copies on the PC are plain text (backup is the developer's job); a push never restarts the app unless "Push and restart" is chosen.

## 2026-10-17 — Deleting tunnels: guarded, DNS left alone
- Decision: `Delete tunnel…` is limited to tunnels whose name parses as raspi-manager's for a known Pi, refused when status is healthy/degraded, shows the routes and the dangling DNS CNAMEs, requires typing the exact tunnel name, and re-checks the status immediately before the DELETE. DNS records and the `cloudflared` service on the Pi are never touched. Verified in Cloudflare's API reference on 2026-10-17: `DELETE /accounts/{account_id}/cfd_tunnel/{tunnel_id}` (no body), the tunnel "must have no active connections", Tunnel Write permission; the docs say nothing about DNS records or about uninstalling the Pi service.
- Why: requested by the developer; deletion is irreversible and would silently break any hostname still pointing at the tunnel.
- Alternatives rejected: deleting the linked DNS records automatically (DELETE on dns_records not verified, and destructive); cascading connection cleanup (not documented here).
- Consequences: the old locally-managed `web-game` tunnel cannot be deleted from the app (foreign name); after deleting a tunnel the developer removes its DNS records and stops the Pi connector by hand until the wizard handles it.

## 2026-10-17 — Several tunnels per Pi: one per project (optional)
- Decision: the tunnel name is `raspi-manager-<pi>` (shared) or `raspi-manager-<pi>--<project>`; the project is typed in Add route… (letters, digits, . _ -). `parse_tunnel_name` recovers (pi, project) by matching known Pi names (so `raspi` and `raspi-v` stay distinct); tunnels not matching are never edited. Requested by the developer (separate projects/domains).
- Why: a tunnel can carry many hostnames, but a project-level tunnel lets one project be stopped, restarted or re-tokened without touching others. Amends the earlier "one tunnel per Pi" decision.
- Planned, not built (Pi wizard F10): one connector per tunnel on the Pi, as a systemd unit `cloudflared-<tunnel>.service` running `cloudflared tunnel run --token …` (the stock `cloudflared service install <token>` creates a single service named `cloudflared`); not yet verified against the docs.
- Consequences: renaming a Pi in hosts.yaml orphans its tunnels (they stay in Cloudflare); the `--` separator in a Pi name would confuse the parser only if two Pis shared a prefix.

## 2026-10-17 — Repoint an existing CNAME to the new tunnel (confirmed action)
- Decision: when Add route… hits an existing hostname whose records are a single CNAME, the GUI offers to repoint it; the plan is recomputed with `repoint=True`, shown with the old target and tunnel status, and applied with `PATCH /zones/{zone}/dns_records/{id}` (body: type, name, content, proxied, ttl, comment). Verified in Cloudflare's docs on 2026-10-17: the PATCH path and the DNS Write permission; the docs do not say whether omitted fields are kept, so type and name are always sent and ttl/comment are copied from the existing record.
- Why: requested by the developer; the existing record most likely points to the old locally-managed tunnel `web-game` while the game moves to `raspi-v`.
- Alternatives rejected: silently overwriting (data-loss risk); only refusing (blocks the migration); deleting + recreating (a window where the name does not resolve).
- Consequences: the old site stops resolving to the old tunnel as soon as the PATCH succeeds, even if the new connector is not installed yet; not run against the real account yet.

## 2026-10-17 — Cloudflare: token in the Windows Credential Manager, one tunnel per Pi created by the API, built before the Pi wizard
- Decision: the API token is stored with `keyring` 25.7.0 (backend `WinVaultKeyring` confirmed on this PC; MIT per most sources, PyPI page not checked; maintained by J. Coombs); account/zone IDs (not secret) go to `~/.raspi/cloudflare.yaml`. Tunnels are created through the API as remotely managed (`config_src: cloudflare`), named `raspi-manager-<pi>`, leaving the old locally-managed `web-game` tunnel on `raspi` untouched. Cloudflare work comes before the generic Pi setup wizard (F10). `httpx` becomes a runtime dependency.
- Why: chosen by the developer (token store, new tunnel, order). API calls verified in Cloudflare's docs on 2026-10-17: create tunnel, token, ingress configuration, DNS CNAME, tunnel list, `/user/tokens/verify`.
- Not verified: whether account-owned tokens verify through `/user/tokens/verify` (so the app does not call it), whether the zone list returns `account.id` (the app falls back to manual entry), the exact permission needed to list zones with a DNS-edit token.
- Alternatives rejected: token in a plain file or typed each time; reusing the old tunnel's credentials; deleting DNS records automatically (left manual).
- Consequences: Add-route is refused instead of overwriting conflicting DNS/tunnels; removing a route leaves its DNS record; the connector (`cloudflared` on the Pi) is not installed by this slice.

## 2026-10-17 — Project renamed to raspi-manager (visible names + commands only)
- Decision: project name `raspi-manager` (pyproject, window titles via `raspi.APP_NAME`, README, launcher `raspi-manager.vbs`, Desktop shortcut `raspi-manager.lnk`). Commands: `raspi-manager`, `raspi` (alias) and `rpim` (alias). Run without a subcommand, the command opens the app window; subcommands and `--help` keep the CLI usage. `[tool.uv.build-backend] module-name = "raspi"` keeps the Python package name.
- Why: the developer chose the name and aliases. The Pis are themselves called `raspi`/`raspi-v`, so docs now show `raspi-manager` to avoid ambiguity.
- Alternatives rejected: renaming the folder, the package (`raspi_manager`) and `~/.raspi` (risky migration: the folder is the session/memory key, configs and env copies would have to move); can be done later as its own decision.
- Consequences: older docs entries may still say `raspi gui`/`raspi web` (the alias works); `~/.raspi` and `%LOCALAPPDATA%
aspi` keep their names.

## 2026-10-17 — Primary UI becomes a native PySide6 (Qt) app; web dashboard kept as secondary
- Decision: `raspi gui` (shortcut "raspi" → `.venv\Scripts\pythonw.exe -m raspi.gui`) is the main interface: live table refreshed every 10 s, SSH in a thread pool (one job per host, so a slow Pi never blocks the others), confirmation dialogs for stop/restart, logs dock, light/dark following Windows. Package `PySide6-Essentials` 6.12.0 (resolved by uv 2026-10-17; Qt widgets only, ~80 MB, LGPL). The FastAPI/Jinja dashboard (`raspi web`) stays working but is no longer where new UI is built.
- Why: the developer found the web dashboard not reactive enough ("pas hyper fan de l'interface web"); native Qt gives instant feedback and background updates without page reloads. Services layer was already UI-agnostic, so the GUI reuses `collect_host`, `run_action`, `read_logs`.
- Alternatives rejected: NiceGUI (still web), Textual (terminal), pywebview (still HTML).
- Consequences: design tokens expressed as Qt style sheet (`gui/theme.py`); a Qt dependency to maintain; the web UI and Qt UI may drift, so new features go to Qt first and web gets only what is cheap. Callbacks from worker threads must be Slots on a GUI-thread QObject with queued connections.
- Sources: [PySide6 on PyPI mirror](https://simple-repository.app.cern.ch/project/pyside6) and QThreadPool/QRunnable worker-signals pattern (pythonguis.com, penguintutor.com), searched 2026-10-17; version actually installed is 6.12.0.

## 2026-10-17 — Windows "app" = Edge app-mode window launched by a script (now secondary)
- Decision: `scripts/raspi-gui.ps1` starts `raspi web` hidden if needed and opens it with `msedge --app=` on a dedicated profile (`%LOCALAPPDATA%\raspi\edge-profile`), stopping the server when the window closes; Desktop shortcut via `-InstallShortcut`.
- Why: native-looking window with zero new dependency and the same UI as the browser; chosen by the developer.
- Alternatives rejected: pywebview (extra dependency, WebView2 to test), tray icon (more code); both possible later.
- Consequences: requires Microsoft Edge (falls back to the default browser, leaving the server running).

## 2026-10-17 — Dashboard mutations protected by token + Host check; `python-multipart` added
- Decision: POST actions require a random per-process token embedded in forms, and every request must carry a localhost Host header. `python-multipart` 0.0.32 added (needed by FastAPI/Starlette to parse HTML forms; resolved by uv 2026-10-17).
- Why: the dashboard can restart services with sudo; a web page open in the same browser could otherwise post to `127.0.0.1` (CSRF) or use DNS rebinding.
- Alternatives rejected: no protection ("localhost only is enough"); full login (overkill for T1).
- Consequences: tokens change at each server start (reload the page after restarting `raspi web`).

## 2026-10-17 — SSH transport: system `ssh` binary through `subprocess` (replaces Fabric/Paramiko)
- Decision: run remote scripts with Windows' OpenSSH (`System32\OpenSSH\ssh.exe`, script sent on stdin to `sh -s`, `BatchMode=yes`, `StrictHostKeyChecking=yes`).
- Why: reuses the Windows ssh-agent, known_hosts and keys that already work (verified today); Git's bundled ssh cannot reach the agent; Paramiko's Windows agent support was not verified; one dependency less.
- Alternatives rejected: Fabric/Paramiko (agent risk, extra deps), asyncssh (not evaluated).
- Consequences: parsing relies on our own scripts; stdin must be bytes on Windows; interactive prompts impossible by design (passwords never go through here).

## 2026-10-17 — Single mode: system-level systemd units run as the login user (supersedes "user units + linger" and the adopted/managed split below)
- Decision: raspi creates and drives `raspi-<app>.service` system units (`User=<login user>`, `EnvironmentFile=`, `WorkingDirectory`, `Restart`), installed with passwordless sudo; existing units are adopted in place and later regenerated by raspi. Logs via `journalctl -u`. No linger needed.
- Why: the existing Pis already use this model and sudo is passwordless on `raspi`; one mode means less code and fewer cases to test; no outage when adopting.
- Alternatives rejected: two modes (user + system: more code), user units only (forces migrating the 3 live apps, needs linger).
- Consequences: raspi requires passwordless sudo on each Pi (checked by `host doctor`; `raspi-v` not yet verified); raspi effectively has root-equivalent power over the Pi through SSH, so the SSH key and the PC must be protected (passphrase + agent).

## 2026-10-17 — Adopt existing services as-is, migrate later (partly superseded: see single-mode entry above; tunnel part still applies)
- Decision: raspi supports two management modes per app: `adopted` (existing system-level units driven with sudo; local-config tunnel pushed over SSH) and `managed` (raspi user units, API-managed tunnel). New apps default to `managed`; migration of an adopted app is an explicit, dry-run-first step.
- Why: the running game on `raspi` must not be interrupted; discovered setup uses system units copied by `up.sh` and a locally-managed tunnel with passwordless sudo available.
- Alternatives rejected: migrate immediately (outage risk); rebuild everything on `raspi-v` first (more work up front; still possible later).
- Consequences: the service layer needs a unit-scope abstraction (user vs system) and a tunnel backend interface (local config vs API); the first vertical slice targets `adopted` read-only status of the three existing apps.

## 2026-10-17 — GUI must cover the whole onboarding (no manual commands)
- Decision: dashboard reaches parity with the CLI and adds guided wizards (add Pi, adopt existing services, new app). Requested by the developer.
- Why: inspection of the existing Pi (see below) showed a hand-made setup (system units copied by `up.sh`, nvm per app, local-config Cloudflare tunnel); onboarding must adopt it, not only create from scratch.
- Consequences: a one-time Pi password may transit through the dashboard in memory (never stored); "adopt" scan is read-only; the "systemd user units" and "API-managed tunnel" decisions may need a compatibility mode (open question in QUESTIONS.md).

## 2026-10-17 — Cloudflare Tunnel managed by raspi through the Cloudflare API
- Decision: one tunnel per Pi, `cloudflared` as systemd user unit, routes (hostname/path → local port) created via API and declarable in app manifests; API token kept outside git.
- Why: needed to expose the Discord game and other web apps without opening ports; Cloudflare recommends remotely-managed tunnels (rules in Cloudflare, manageable by API) and requires a final catch-all ingress rule ([Cloudflare Tunnel docs](https://developers.cloudflare.com/tunnel/configuration/), checked via search 2026-10-17; exact API endpoints not yet verified).
- Alternatives rejected: local YAML config + manual DNS (less automated); fully manual Cloudflare dashboard (not "flexible").
- Consequences: needs a domain on Cloudflare and an API token; Access/WAF/load-balancing deferred.

## 2026-10-17 — Dashboard direction "Ops Ledger"
- Decision: sober, dense, technical; custom hybrid inspired by minimal-suisse; IBM Plex Sans/Mono; light+dark auto; English UI. Details in DESIGN.md.
- Why: expert single user, many status rows at a glance. Status never by colour alone (WCAG 1.4.1).
- Alternatives rejected: airy Ma/Japan style (too sparse), neobrutalism (tiring), generic SaaS look.

## 2026-10-17 — Stack: Python (uv, Typer, Fabric/Paramiko, FastAPI+HTMX)
- Decision: single language for CLI and dashboard; dashboard server-rendered, bound to localhost.
- Why: same language as the apps, good SSH libraries, no front-end build. Versions to be pinned at scaffold after online check (searches 2026-10-17 gave conflicting FastAPI numbers, 0.136–0.141, and no Typer/HTMX versions).
- Alternatives rejected: Node/TS (weaker SSH), Go (more dashboard work), Ansible (too heavy for 2–5 hosts), native Windows GUI.

## 2026-10-17 — No agent on the Pi: pure SSH
- Decision: all control over SSH; live state read from systemd and git each time.
- Why: nothing to install/secure/update on the Pi; apps keep running when the PC is off.
- Alternatives rejected: Pi agent with API (more code, attack surface); Tailscale deferred.

## 2026-10-17 — Process supervision: systemd user units + linger (SUPERSEDED by single-mode system units)
- Decision: one `raspi-<app>.service` user unit per app, `EnvironmentFile`, `loginctl enable-linger`, logs via `journalctl --user`.
- Why: native, no root for daily ops; Raspberry Pi docs recommend lingering on headless setups ([Arch wiki systemd/User](https://wiki.archlinux.org/title/Systemd/User), search 2026-10-17).
- Alternatives rejected: system-level units (sudo each action), Docker/PM2/supervisor (extra layer).

## 2026-10-17 — Env vars: `.env` files on the PC outside git
- Decision: `~/.raspi/env/<app>.<host>.env`, pushed as `chmod 600` files. Backup is the developer's job (backup command planned later).
- Alternatives rejected: sops/age (more tooling), Pi-only storage (lost with the SD card).

## 2026-10-17 — Git access: read-only deploy key per repo
- Decision: Pi clones private repos through deploy keys.
- Alternatives rejected: PC pushes files (no autonomous pull), PAT on the Pi.

## 2026-10-17 — Tier T1; runtimes Python and Node
- Decision: personal tool; apps in Python (venv) and Node.

## Pending
- Pin dependency versions at scaffold time.
- Confirm Raspberry Pi OS Python version (assumed 3.13 on trixie, unverified).
