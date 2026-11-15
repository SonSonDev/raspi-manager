# DESIGN

## Choix du skill design-ui

- **Date**: 2026-10-17 · **Mode**: init (called from project-kickoff) · **Direction**: custom hybrid · **Method**: `methodes/impeccable.md` (product register)
- **Direction name**: "Ops Ledger" — a dense, ruled, typographic control panel.
  - Borrows (max 3) from `minimal-suisse`: visible grid + 1px rules instead of shadows/cards; single grotesque family; left-aligned flag text.
  - Excluded: oversized airy layouts (Ma/Japan), neobrutalist borders/offset shadows, Material/Bootstrap look, a single red-only accent (red is reserved for errors here, see tokens).

### Users, tasks, platform
- **User**: me alone (developer), expert, desktop browser on `localhost`, short frequent sessions ("is everything up? restart this").
- **Platform**: desktop browser first. Must stay usable down to 390 px (read status, restart), but no mobile-first effort. Local only, no auth UI in T1.
- **3 main tasks (frequency)**:
  1. See fleet state at a glance: every Pi × app, status, deployed version (very often).
  2. Act on an app: start / stop / pause / restart, read recent logs (often).
  3. Deploy / change version or edit env vars of an app (occasionally).
- **Action budget**: task 1 = 0 actions (landing page); task 2 = 1 click on the row action + inline result (logs = 1 click, inline drawer); task 3 = ≤ 3 actions (open app → choose version/edit var → confirm).

### Tone and anti-goals
- **Tone**: sober · dense · technical.
- **Not**: generic SaaS, decorative, gamified, "friendly" illustrations, gradients, card-in-card, emoji icons.

### Constraints
- UI language: **English**. Light + dark following `prefers-color-scheme`. No brand imposed. Server-rendered (FastAPI + HTMX): no heavy JS, must feel instant (< 400 ms feedback, spinner beyond).

### Layout
- Wide screens (≥ 840 px): left rail listing Pis (with a one-glyph health mark), main area = table of apps for the selected Pi, or "All" view grouped by Pi. Detail opens as a right drawer (logs, env, versions) without leaving the list.
- Medium/compact: rail collapses to a top selector; table becomes stacked rows; actions stay as text buttons ≥ 44 px.
- Row actions: restart is the primary inline action; stop/pause secondary; destructive (stop, rollback, delete env) smaller, separated and confirmed.
- Keyboard: `/` filter, `j/k` move, `r` restart (confirm with Enter), `l` logs, `Esc` closes drawer.

### Native app: menus + one slim toolbar, platform look (2026-11-15)
**Direction change for the Qt app** (the web dashboard keeps "Ops Ledger"): the custom stylesheet and the 17 same-looking buttons were judged ugly (too tall, irregular, still too many buttons, style in general). The app now looks native: Qt's `windows11` style, system fonts, system light/dark and accent; a minimal stylesheet only adds the row-wide selection, comfortable toolbar buttons, the error banner and the monospace logs.
- **Menu bar** (rare actions, one menu away): **Pi** (Check Pi… · Scan apps… · Publish apps to Pi… | Add a Pi… · Edit Pi… | Remove Pi…), **App** (New app… · Continue setup… | Deploy key… · Install app… · Environment… · Install unit… | Update…), **Cloudflare** (Set up route… · Cloudflare…).
- **One slim toolbar** (daily use): Restart · Start · Stop · Logs | Environment · Update … Refresh (right). Disabled actions are greyed with a reason in the tooltip. Restart and Start stay mutually exclusive.
- Every command is one QAction shared by its menu and the toolbar, so shortcuts are unchanged (F5, Ctrl+R, Ctrl+S, Ctrl+L, Ctrl+E, Ctrl+N, Ctrl+Shift+E, Ctrl+Shift+C) and exist once.
- The table's **Next step** column sits right after Host and takes the free width; the state glyph + word column stays the anchor.
- Action count: Restart, Stop, Logs, Environment, Update, Refresh, Start = 1 click (as before); setup actions and Pi actions = 2 clicks (menu, item) instead of 1, deliberate for rare actions (Hick).
Rejected: three labelled rows of 17 buttons (tried the same day, judged ugly); a detail side panel (rework); right-click only (not discoverable).

### States
Status is never conveyed by colour alone: always glyph + word.

| State | Glyph + label | Colour token |
|---|---|---|
| running | ● running | `--ok` |
| stopped | ○ stopped | `--muted` |
| paused (SIGSTOP/freeze) | ‖ paused | `--warn` |
| failed / crash-loop | ▲ failed | `--err` |
| unreachable Pi | ✕ unreachable | `--err` |
| deploying / unknown | ◌ working… | `--info` |

Also design: loading (skeleton rows), empty (no app → show the exact CLI command to add one), error (what failed + how to fix, with SSH stderr excerpt), success (inline row flash, no toast pile-up).

### Starting tokens
| Token | Light | Dark | Use / text on it |
|---|---|---|---|
| `--bg` | `#FFFFFF` | `#0E1013` | page |
| `--ink` | `#111418` | `#E7E9EC` | text, rules |
| `--muted` | `#5B6168` | `#9AA1A9` | secondary text |
| `--rule` | `#D5D8DC` | `#2A2F35` | 1px separators |
| `--accent` | `#0B57D0` | `#7AA7FF` | links, focus ring, primary action; text on it: `#FFFFFF` (light) / `#0E1013` (dark) |
| `--ok` | `#1B6E3A` | `#5FD08A` | running |
| `--warn` | `#8A5A00` | `#E3B341` | paused |
| `--err` | `#B3261E` | `#FF8A80` | failed / unreachable |
| `--info` | `#5B6168` | `#9AA1A9` | working |

All pairs must reach WCAG AA (4.5:1 text, 3:1 components). The native Qt app takes its text, background and button colours from the system palette (contrast is the platform's); only the table's state colours, the banner and the focus accent come from these tokens (ink 18.5 / 15.7, muted 6.3 / 7.3, error 6.5 / 8.4, accent 6.4 / 8.0 on white / near-black, measured 2026-11-15). Still to measure: ok, warn, info on the system's dark table background.

- **Type**: IBM Plex Sans (UI) + IBM Plex Mono (logs, versions, hostnames, env keys) — open licence (SIL OFL). Scale 12 / 13 / 14 (base) / 16 / 20 / 28 px; line-height 1.45 text, 1.15 titles. `font-variant-numeric: tabular-nums` on versions and counters.
- **Spacing**: 4 px unit; row height 36 px desktop (44 px under `pointer: coarse`).
- **Radius**: 2 px max. **Shadows**: none (1px rules; drawer separated by a rule).
- **Motion**: 120 ms ease-out for drawer and row flash; none under `prefers-reduced-motion`.

### Assumed aesthetic risk
Very high information density with almost no ornament may feel austere. **Guardrail**: strong size/weight hierarchy, generous row height (36 px), and the status glyph column as the one consistent visual anchor; if scanning feels tiring in real use, raise row height before adding decoration.

### Research sources
Light pass (2026-10-17, T1 budget); no NN/g article surfaced, so no NN/g claim is made.
- Status must not rely on colour alone (WCAG 1.4.1); pair colour with label + shape, same colour = same meaning everywhere; icon once per context. Sources: [Dynatrace status & health](https://developer.dynatrace.com/design/status-and-health), [DEHN indicator](https://designsystem.dehn.de/atoms/indicator/), [WCAG 1.4.1 summary](https://www.disabilityworld.org/toolkit/standards/wcag/1-4-1-use-of-color/). Kept: glyph+word status column.
- A bare status gives too little context to act on ([Data & Design, statuses beyond the happy path](https://dataanddesign.substack.com/p/three-questions-to-ask-when-designing)). Kept: errors show cause + next step.
- Dokploy/Coolify comparisons report stale or laggy status in their dashboards ([lumadock comparison](https://lumadock.com/tutorials/coolify-vs-dokploy)) → we always read live state over SSH on load/refresh and show "last checked" time. Kept. Not researched: visual details of Portainer/Cockpit/Uptime Kuma (no usable source found).

### Direction change log
- 2026-10-17 — initial direction "Ops Ledger" chosen.
- 2026-11-15 — native Qt app: tried three labelled rows of buttons with the custom stylesheet (judged ugly), replaced by a menu bar + one slim toolbar in the platform's own Windows 11 look; Next step column after Host. "Ops Ledger" stays the direction of the web dashboard.
