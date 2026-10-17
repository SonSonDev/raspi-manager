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

All pairs must reach WCAG AA (4.5:1 text, 3:1 components) — **to be measured at apply time**, values above are starting points, not yet verified.

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
