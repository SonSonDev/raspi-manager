# raspi

Manage personal apps on several Raspberry Pi from a Windows PC, over SSH, with no agent on the Pi.

> **Status: design phase.** Only the documentation and the SSH bootstrap script exist. The `raspi` CLI and dashboard are not implemented yet (see [SPEC](docs/SPEC.md) for planned features and their status).

## What it will do
- Deploy apps (Python, Node) from private GitHub repos to one or more Pi, pinned to a tag, branch or commit, with rollback.
- Run them permanently as systemd services: start, stop, pause, resume, restart, logs.
- Manage per-app environment variables (kept on the PC, outside git, pushed as `chmod 600` files).
- Route web apps to the internet through a Cloudflare Tunnel managed via the Cloudflare API (hostname → local port).
- Offer a CLI and a small local web dashboard (`127.0.0.1` only) showing every Pi and app at a glance.

## Available today: prepare a Pi for SSH key access
```powershell
.\scripts\setup-pi-ssh.ps1 -PiHost raspi.local -User <user>
.\scripts\setup-pi-ssh.ps1 -PiHost raspi.local -User <user> -CheckOnly   # verify only
```
It checks the OpenSSH tools and `ssh-agent`, creates/loads your key, installs it on the Pi and verifies passwordless login. Details and troubleshooting: [docs/PI-SETUP.md](docs/PI-SETUP.md).

## Planned stack
Python (`uv`, Typer, Fabric/Paramiko, FastAPI + HTMX, pytest, ruff). Versions are pinned when the project is scaffolded.

## Documentation
| File | Content |
|---|---|
| [docs/BRIEF.md](docs/BRIEF.md) | Goal, scope, constraints, risks |
| [docs/SPEC.md](docs/SPEC.md) | Features and their status |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Planned code map and data flows |
| [docs/DESIGN.md](docs/DESIGN.md) | Dashboard design direction and tokens |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Decision log |
| [docs/QUESTIONS.md](docs/QUESTIONS.md) | Open questions |
| [docs/PI-SETUP.md](docs/PI-SETUP.md) | SSH setup for a new Pi |
| [CLAUDE.md](CLAUDE.md) | Working rules for Claude Code |

## Security notes
No passwords or passphrases are stored by the tooling. Secrets (env files, Cloudflare token) live in `~/.raspi/`, outside the repository. The dashboard has no authentication and must stay on localhost.
