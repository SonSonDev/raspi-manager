# CLAUDE.md — raspi (Python CLI + local web dashboard, Windows PC → Raspberry Pi over SSH)

## Context
- Developer: solo, maintains personal projects on a Windows PC, pushes to private GitHub repos.
- Talk to the developer in French. Code, identifiers, comments, docs, UI text, commits in English.
- Target: tool runs on the Windows PC; it manages 2–5 Raspberry Pi (Raspberry Pi OS Lite 64-bit, Debian 13 trixie) over SSH, no agent on the Pi.
- Tier: **T1** personal tool (see DECISIONS.md).

## Stack (chosen 2026-10-17 — re-check versions online before pinning or upgrading)
- Python managed with `uv`; **Typer** (CLI), system `ssh` via `subprocess` (SSH, see DECISIONS), **FastAPI + Jinja2 + HTMX** (dashboard, server-rendered, `127.0.0.1` only), **PyYAML**, **httpx** (Cloudflare API), **pytest**, **ruff**. Resolved by `uv` from PyPI on 2026-10-17 (locked in `uv.lock`): Python 3.12, typer 0.27.3, fastapi 0.143.3, uvicorn 0.54.0, pyyaml 6.0.3, python-multipart 0.0.32, pytest 9.1.1, ruff 0.17.1, uv 0.13.1 (installed with `pip --user`). `httpx` is a dev dependency for now (test client); HTMX not added yet. **Native UI: PySide6-Essentials 6.12.0 (Qt widgets, LGPL)**; GUI tests run with `QT_QPA_PLATFORM=offscreen` (no fonts/stylesheet there, so use a real-platform capture to judge looks).
- On the Pi: system-level systemd units (run as the login user, passwordless sudo), `git`, `uv`/Python, Node (per app), `cloudflared`.
- **Library policy**: anything not listed needs approval. Before adding: latest stable version, maintenance, publisher, licence; say what was added and why; log in DECISIONS.md.
- **Version freshness**: when touching dependencies and at each milestone, compare with current stable releases. Never claim "verified" without checking online that session.

## Project documentation
- @docs/BRIEF.md — goal, ambition, constraints (the why)
- @docs/SPEC.md — features, behaviour, scope, status
- `docs/ARCHITECTURE.md` — code map and data flows (read when touching structure)
- `docs/DESIGN.md` — dashboard design direction and tokens (read before any UI work; use the `design-ui` skill in `apply` mode)
- `docs/DECISIONS.md` — decision log, newest first
- `docs/QUESTIONS.md` — doubtful behaviours awaiting an answer
- **Docs are always in sync with the code, without being asked, in the same change:** behaviour → SPEC · structure → ARCHITECTURE · any technical/product choice (even in discussion) → DECISIONS. End each task by stating which docs were updated (or none). If doc and code disagree, say so.
- Each doc < ~400 lines; archive superseded decisions to `docs/archive/`.

## Architecture rules
- `core/` is pure logic (manifests, inventory, unit-file rendering, route planning): no I/O, no SSH, fully unit-testable.
- All SSH goes through `ssh/` behind a small transport interface; all Cloudflare HTTP goes through `cloudflare/`. CLI and web are thin and call the same `core` services — no logic duplicated between them.
- Live state is read from the Pi (systemd, git) every time; never cache it as truth.
- Never `shell=True`-style string concatenation of user values into remote commands: quote with `shlex.quote` / argument lists.
- Files < 400 lines; no commented-out code; comments explain *why*.

## UX & design rules
- Main UI is the Qt app in `src/raspi/gui/` (tokens in `gui/theme.py`); never block the GUI thread: SSH only through `gui/workers.submit`.
- Follow `docs/DESIGN.md` ("Ops Ledger"): dense, sober, status = glyph + word (never colour alone), light/dark auto, English UI, WCAG AA (contrast to be measured), keyboard shortcuts.

## Quality gates (a task is not done until these pass; say which were run)
- `uv run ruff check . && uv run ruff format --check .`
- `uv run pytest`
- UI changes: screenshots at 390 / 768 / 1280 px, light + dark.

## Data safety & security
- Never commit secrets: `~/.raspi/` (env files, Cloudflare API token, tunnel tokens) lives outside the repo; `.gitignore` guards stray `.env`/tokens.
- Remote env files `chmod 600`; deploy keys read-only, one per repo; SSH host keys verified; no password auth.
- Dashboard binds `127.0.0.1` only; no auth in T1 — do not expose it.

## Safety (environment)
- Destructive remote actions (stop, rollback, delete env/route/tunnel, apt upgrades) require explicit confirmation in CLI (`--yes` to skip) and UI.
- Dry-run (`--dry-run`) for deploy/route/provision commands that mutate a Pi or Cloudflare.
- **Claude never modifies a Pi** (no migration, config/unit edits, installs, restarts, new processes). Read-only SSH inspection only; the developer does all changes through the raspi GUI. When a project needs adapting to raspi, say so instead of doing it.
- Test against a real Pi only when the developer says which host; never touch a Pi or Cloudflare zone unprompted.

## Working agreement
- Ask when it is the developer's call (behaviour, scope, new dependency, tier change); otherwise decide and say so. Recommend, don't survey.
- Small reviewable steps; commit only when asked (imperative English messages).
- Check whether the OS/an approved library already does it before building.
- Milestone check (`/project-checkpoint`) each release or ~20 commits.

## Commands (to be confirmed at scaffold time)
`uv` is not on PATH: use `python -m uv ...`.
- `python -m uv sync` · `python -m uv run pytest` · `python -m uv run ruff check .` · `python -m uv run ruff format .`
- `python -m uv run raspi gui` (native app; Desktop shortcut via `scripts/install-shortcut.ps1`)
- `python -m uv run raspi status` (read-only live state) · `raspi logs <app> -n 50` (read-only) · `raspi start|stop|restart <app> [--dry-run] [--yes]` (**changes a Pi: only the developer runs these, never Claude**) · `python -m uv run raspi web` (http://127.0.0.1:8765)
- Every `subprocess` call from the GUI must pass `creationflags=CREATE_NO_WINDOW` (done in `ssh/transport.py`, covered by `tests/test_transport.py`), otherwise Windows flashes a console window per call.
- Config: `~/.raspi/hosts.yaml` (hosts and their apps). Subprocess input to ssh must be bytes (Windows would otherwise send CRLF to `sh`).
