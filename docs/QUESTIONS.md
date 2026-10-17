# Open questions
Doubtful behaviours awaiting the developer's answer. Ask, never decide silently. Remove once answered (and record the answer in SPEC / DECISIONS).
- [ ] Do you already own a domain managed on Cloudflare? — routing needs it. Recommendation: yes, one domain with subdomains per app.
- [ ] How does each of your repos start (run command, port)? — manifest needs it; start with the Discord game repo.
- [ ] Which Pi models / OS images do you run (Pi 3/4/5, 32 or 64-bit)? — affects Node/wheels availability. Recommendation: 64-bit Lite.
- [ ] Is the Discord game a Discord Activity (embedded iframe) or a plain web page with OAuth? — Activities add URL-mapping and WebSocket requirements (not yet researched).
- [ ] Pi `raspi` (192.168.1.20) is armv7l 32-bit, Raspbian 11 bullseye, Python 3.9.2 (checked 2026-10-17) — reinstall as 64-bit trixie, or keep? Recommendation: keep for now if your apps run on Python 3.9 and the Node version you need; reinstall only if blocked. `raspi-v` (192.168.1.21) not yet checked.
- [ ] Which Python and Node versions do your apps require? — decides the minimum supported on the Pi.
- [ ] Should `pause` be SIGSTOP (keeps memory) or just stop? — SIGSTOP may break WebSocket apps. Recommendation: offer SIGSTOP, warn in docs.
- [ ] How many old releases to keep per app? — recommendation: 3.
