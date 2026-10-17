# Open questions
Doubtful behaviours awaiting the developer's answer. Ask, never decide silently. Remove once answered (and record the answer in SPEC / DECISIONS).
- [x] Domain on Cloudflare: yes, `nanmonee.com` (seen in the existing tunnel config on `raspi`; tunnel `web-game` → `web-game.nanmonee.com` → `localhost:2567`, locally-managed, `cloudflared` as a root system service with `/etc/cloudflared/config.yml`).
- [x] (answered 2026-10-17: adopt as-is, migrate later; see DECISIONS.md) Existing apps on `raspi` run as **system** units (`/etc/systemd/system`, copied by `~/dev/systemd/up.sh`, passwordless sudo available) and the tunnel is **locally managed**. Do we (a) adopt them as-is (system units + local tunnel config pushed over SSH), (b) migrate them to user units + API-managed tunnel, or (c) adopt now, migrate later? Recommendation: (c).
- [ ] How does each of your repos start (run command, port)? — manifest needs it; start with the Discord game repo.
- [ ] Which Pi models / OS images do you run (Pi 3/4/5, 32 or 64-bit)? — affects Node/wheels availability. Recommendation: 64-bit Lite.
- [ ] Is the Discord game a Discord Activity (embedded iframe) or a plain web page with OAuth? — Activities add URL-mapping and WebSocket requirements (not yet researched).
- [ ] Pi `raspi` (192.168.1.20) is armv7l 32-bit, Raspbian 11 bullseye, Python 3.9.2 (checked 2026-10-17) — reinstall as 64-bit trixie, or keep? Recommendation: keep for now if your apps run on Python 3.9 and the Node version you need; reinstall only if blocked. `raspi-v` (192.168.1.21) not yet checked.
- [ ] Which Python and Node versions do your apps require? — decides the minimum supported on the Pi.
- [ ] Should `pause` be SIGSTOP (keeps memory) or just stop? — SIGSTOP may break WebSocket apps. Recommendation: offer SIGSTOP, warn in docs.
- [ ] How many old releases to keep per app? — recommendation: 3.
