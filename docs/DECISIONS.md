# Decision log
One entry per decision, newest first. A decision is only changed by a new entry that supersedes it.
Not logged: visual tweaks, tuning values, reverted trials.

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

## 2026-10-17 — Process supervision: systemd user units + linger
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
