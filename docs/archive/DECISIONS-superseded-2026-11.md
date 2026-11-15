# Decisions superseded or reversed (archived 2026-11-15)
Moved out of DECISIONS.md unchanged. Each one was replaced by a later entry named in its own text.

## 2026-11-15 — Toolbar: three labelled rows by frequency, no menus (superseded: judged ugly, replaced the same day)
- Decision: the 17 same-looking buttons are grouped into three labelled rows (Run, App in workflow order, Pi) with weight by frequency (bordered, quiet, red for destructive); the table's Next step column moves right after Host. No button goes into a menu. A `--edge` token (≥ 3:1) is used for button borders.
- Why: the developer found many buttons alike; the grouping follows the tasks and the workflow order, keeps every action at 1 click and the shortcuts as they were (DESIGN.md: never hide a frequent action).
- Alternatives rejected: a contextual “Next” button plus App/Pi menus (recommended by Claude, not chosen: +1 click for rare actions, hidden actions); a side panel (width, rework).
- Consequences: the toolbar is three lines tall (about 90 px); the window is no longer truncated at 1000 px; a later “Next” button can still be added without moving anything.

## 2026-11-14 — Pis are registered by hand; the Pi CRUD is removed from the GUI and CLI
- Decision: removed “Add a Pi…”, “Edit Pi…”, “Remove Pi…” (GUI) and `raspi host add|edit|rm` (CLI), with their dialog, writer functions and tests. A Pi is added by editing `~/.raspi/hosts.yaml`; `docs/PI-SETUP.md` documents the terminal commands (ssh key, first-connection host key check with fingerprint comparison, file format). `raspi host ls` stays.
- Why: the developer asked for terminal instructions instead of in-app Pi management; a Pi needs SSH and host-key trust set up outside the app anyway, and the app cannot safely accept host keys for the user.
- Alternatives rejected: in-app host-key trust (`ssh-keyscan` + fingerprint confirmation, on “Add a Pi” or on error); keeping the CLI twin for scripting.
- Consequences: supersedes the two 2026-11-14 entries below (first-run “Add a Pi”, edit/remove) and weakens the GUI parity rule for Pis (F7b wizard still planned, to be reconsidered). The first-run state stays: a how-to panel in the window (commands, YAML example, “Open setup guide” button) shown when there is no hosts file or it is empty; real config errors still use the red banner. Possible next step, not decided: a read-only scan of a Pi's apps and a manifest file on the Pi.

## 2026-10-17 — Windows "app" = Edge app-mode window launched by a script (now secondary)
- Decision: `scripts/raspi-gui.ps1` starts `raspi web` hidden if needed and opens it with `msedge --app=` on a dedicated profile (`%LOCALAPPDATA%\raspi\edge-profile`), stopping the server when the window closes; Desktop shortcut via `-InstallShortcut`.
- Why: native-looking window with zero new dependency and the same UI as the browser; chosen by the developer.
- Alternatives rejected: pywebview (extra dependency, WebView2 to test), tray icon (more code); both possible later.
- Consequences: requires Microsoft Edge (falls back to the default browser, leaving the server running).

## 2026-10-17 — Process supervision: systemd user units + linger (SUPERSEDED by single-mode system units)
- Decision: one `raspi-<app>.service` user unit per app, `EnvironmentFile`, `loginctl enable-linger`, logs via `journalctl --user`.
- Why: native, no root for daily ops; Raspberry Pi docs recommend lingering on headless setups ([Arch wiki systemd/User](https://wiki.archlinux.org/title/Systemd/User), search 2026-10-17).
- Alternatives rejected: system-level units (sudo each action), Docker/PM2/supervisor (extra layer).

