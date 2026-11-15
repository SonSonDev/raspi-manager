# Open questions
Doubtful behaviours awaiting the developer's answer. Ask, never decide silently. Remove once answered (and record the answer
in SPEC / DECISIONS / WORKFLOW). Cleaned on 2026-11-15: answered, obsolete or superseded items were removed (domain
`nanmonee.com` on Cloudflare; adopt-then-migrate; passwordless sudo on `raspi-v` is on; the DNS conflict is handled by
“Repoint it…”; the `releases/` question is void since there is no releases layout).

## Apps and Pis
- [ ] **Run command and port of each repo**: Notes App is known (`npm start`, Node 26). Still to fill in: `web-game`, `example-bot`, `chat-bot`. Which Node/Python versions do they need?
- [ ] **`raspi` (192.168.1.20)** is armv7l 32-bit, Raspbian 11, Python 3.9.2 (checked 2026-10-17), 1 GB: reinstall as 64-bit trixie, or keep? Recommendation: keep unless an app is blocked (Node 26 has no 32-bit build). `raspi-v` is aarch64, Debian 13, 4 GB (checked 2026-11-15).
- [ ] **Discord game**: a Discord Activity (embedded iframe) or a plain web page with OAuth? Activities add URL mapping and WebSocket needs (not researched).
- [ ] **`web-game` on `raspi-v`**: needs `ffmpeg`, `yt-dlp` (standalone binary or pipx), two `.env` files, port 2567, hostname `web-game.nanmonee.com` (ffmpeg and yt-dlp: Pi → Install tools…, 2026-11-15). Does it need the `bgutil-ytdlp-pot-provider` seen on `raspi`, or only plain yt-dlp? Where is the Vite client built (Pi or PC)? Its root `.env` also holds a bot token and client secret the client should not need.
- [ ] **`web-game` is stopped on `raspi`** (journal 2026-10-17) and the old `cloudflared.service` there loops (IPv6 edge failures); `web-game.nanmonee.com` had no DNS record then. Should the game be online now, and should the old tunnel be retired from the GUI?
- [ ] **`chat-bot`** holds its bot token as a string literal in `index.js` (not under git, value not displayed). To manage it: read `process.env.DISCORD_BOT_TOKEN`, create its `.env`, declare it. Consider regenerating the token.
- [ ] **Journals**: `raspi logs` shows “No entries” for `example-bot` and `web-game`, and journald uses ~18% of RAM on the 1 GB Pi: look at it when migrating.

## Cloudflare
- [ ] **First real run** (nothing done on the real account yet): create an API token (Account › Cloudflare Tunnel: Edit, Zone › DNS: Edit, plus Zone › Zone: Read if the look-up fails; limited to the account and `nanmonee.com`), paste it in “Cloudflare…”, look up the zone. Then “Add route…” or “Set up route…” shows the exact changes before applying.

## Product
- [ ] **`pause`**: SIGSTOP (keeps memory, may break WebSocket apps) or just stop? Recommendation: offer SIGSTOP and warn.
- [ ] **uv on the Pi**: installer script, release binary or apt? Only needed for Python apps.
