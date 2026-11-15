# Brief
- **Goal / what it does**: from the PC, deploy and run personal apps (Python, Node; private GitHub repos) permanently on several Raspberry Pi: version control, environment variables, start / stop / pause / restart, logs. Also route some web apps to the internet through Cloudflare, including a Discord game (web app + OAuth/Activity, inbound HTTPS). User words: "les lancer de manière permanente sur mes raspi, contrôler leur version, leur variable d'environnement, les lancer, les mettre en pause, les redémarrer".
- **Audience & what a good day looks like**: me alone. One command or one click deploys a pushed version; the dashboard shows at a glance that everything is up with the right version; routing a new app to a hostname takes one command.
- **Ambition / tier**: T1 personal tool.
- **Scale**: 2–5 Pi with different roles, a handful of apps each, one developer, long-lived.
- **Interfaces**: native Windows app (main), CLI (scriptable twin), small local web dashboard (secondary, localhost only).
- **Constraints**: SSH only, no agent on the Pi; private repos via read-only deploy keys; env files on the PC outside git; system-level systemd services run as the login user (passwordless sudo required); Cloudflare Tunnel managed through the Cloudflare API (requires a domain on Cloudflare); Windows 11 PC.
- **Non-goals**: agent on the Pi, public/multi-user dashboard or auth, encrypted-secrets tooling (sops), Docker orchestration, push-triggered CI/CD, historical metrics, mobile app, Cloudflare Access/WAF and multi-Pi load-balancing (T1).

## Risks & assumptions to validate
| Assumption / risk | Impact if wrong | How to validate | Status |
|---|---|---|---|
| OpenSSH client / ssh-agent usable from Python on Windows | Blocks all remote ops | Prototype: Fabric connect to one Pi | validated 2026-11-15: the system `ssh` through `subprocess` works from the GUI and CLI on `raspi` and `raspi-v` (DECISIONS 2026-10-17) |
| App launch command can be declared in a manifest field for every repo | Deploy can't start some apps | Try on the Discord game repo and one more | partly validated 2026-11-15: `build` and `run` fields exist; Notes App (`npm ci && npm run build`, `npm start`) fits; the Discord game not tried yet |
| `pause` via SIGSTOP is acceptable (memory kept, sockets stay open; may break WebSocket apps) | Surprising behaviour | Document; test on the game | open |
| Cloudflare API token scope and tunnel/DNS API behave as documented | Routing feature blocked | Prototype `route add` on a test hostname | open |
| Discord Activity needs only a public HTTPS hostname (+ WebSocket) routed to the app | Extra routing features needed | Check Discord Activities docs when routing the game | open (not yet researched) |
| Building deps (Python wheels, Node) on arm64 Pi is fast enough | Slow deploys | Measure on first real deploy | open: Notes App's first build on `raspi-v` (aarch64, 4 GB) not run yet |
| `.env` files exist only on the PC | Loss if disk dies | Accepted; add backup command later | mitigated: the Pi's own .env stays the reference and its app inventory lives on the Pi (see DECISIONS 2026-11-14) |
