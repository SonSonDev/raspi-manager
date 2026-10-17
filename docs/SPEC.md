# Product specification
Source of truth for scope and behaviour. Status: `planned` · `in progress` · `done` · `dropped`.

## Overview
`raspi` is a CLI plus local dashboard (`raspi web`, `127.0.0.1`) to manage apps on several Raspberry Pi over SSH. Config lives in `~/.raspi/` on the PC. The real state is always read from the Pi.

## Out of scope
Agent on the Pi, dashboard auth/remote exposure, sops/encrypted secrets, Docker, webhook/CI auto-deploy, metrics history, mobile, Cloudflare Access/WAF, multi-Pi load balancing.

## Interfaces
- CLI: `raspi <group> <verb>`; all mutating commands support `--dry-run`; destructive ones ask confirmation (`--yes`).
- Dashboard: landing page = all Pis × apps (status, version, URL, last checked); right drawer for logs / env / versions. See DESIGN.md.

## F1 — Host inventory & provisioning
Status: `planned`
- `raspi host add/ls/rm`, entries in `~/.raspi/hosts.yaml` (name, address, user, role).
- Prerequisite, done before `host add`: key access via `scripts/setup-pi-ssh.ps1` (see `docs/PI-SETUP.md`); `host doctor` re-checks the same things.
- `raspi host doctor <host>`: checks SSH, host key, linger, git, uv, Node, cloudflared, disk space; says how to fix.
- `raspi host provision <host>`: enables linger, creates `~/apps`, installs missing prerequisites (confirmed).

## F2 — App definition
Status: `planned`
- `~/.raspi/apps/<app>.yaml`: repo URL, runtime (`python` | `node`), install command, run command, target hosts, optional routes.
- Deploy key generated per repo/host; `raspi app key <app>` prints the public key to add on GitHub.

## F3 — Deploy & versions
Status: `planned`
- `raspi deploy <app> [--host H] [--ref tag|branch|sha]`: fetch, checkout into `releases/<sha>`, install deps, switch `current`, restart. Keeps last N releases.
- `raspi rollback <app>`; `raspi versions <app>` lists deployed vs. available refs.
- Failed install leaves the running release untouched.

## F4 — Lifecycle & logs
Status: `planned`
- `start | stop | restart | pause | resume | status | logs [-f] [--since]` per app and host (systemd user unit `raspi-<app>.service`).
- `pause` = SIGSTOP, `resume` = SIGCONT; status shows `paused`.

## F5 — Environment variables
Status: `planned`
- Source of truth: `~/.raspi/env/<app>.<host>.env` (outside git). `raspi env set/get/ls/unset/push`; values masked by default in output.
- Push writes the remote env file `chmod 600` and restarts only on request.

## F6 — Cloudflare routing
Status: `planned`
- One Cloudflare Tunnel per Pi, `cloudflared` as a systemd user unit; tunnel and routes created via the Cloudflare API with an API token stored in `~/.raspi/`.
- `raspi route add <hostname> <app>[:port] [--path P]`, `route ls`, `route rm`; routes also declarable in the app manifest (several hostnames/paths per app); catch-all `http_status:404` kept last.
- Dashboard shows public URL and tunnel state per app.
- Target use cases: the Discord game (web app + OAuth/Activity) and other web projects.

## F7 — Dashboard
Status: `planned`
- Fleet view, row actions (restart primary; stop/pause secondary; destructive confirmed), logs drawer, env editor, deploy/version picker, keyboard shortcuts. States: loading / empty (shows CLI command) / error (cause + fix) / success.

## F8 — Fleet health & extras (later)
Status: `planned`
- Pi temperature/disk/memory, grouped `apt upgrade`, `.env` backup. Not before F1–F7 work.

## Open questions
See QUESTIONS.md.
