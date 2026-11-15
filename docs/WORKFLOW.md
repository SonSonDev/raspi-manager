# Workflow — from a bare Pi to an app online and kept up to date
Single reference for the end-to-end path. SPEC says *what each feature does*; this file says *in which order, with which
prerequisites, and how each step is checked*. **Any new step or button is written here first** (rule at the bottom).
Status marks: ✅ built · 🟡 partly · ⬜ not built. Everything marked ✅ was tested with a fake Pi unless noted; the first
real run on `raspi-v` is listed in "Real-Pi log".

## 1. Vocabulary
- **Pi**: a machine in `~/.raspi/hosts.yaml` (name, address, login user). **App**: an entry in the Pi's `~/.raspi/apps.yaml`.
- **Declared fields** of an app (all in the Pi's `apps.yaml`, never secrets): `name`, `unit`, `repo` (`owner/name`),
  `build`, `run`, `path`, `env_files`. They are asked **once**, in one form, and every later step reads them from there.
- **Stage**: how far an app has come. Always *read from the Pi* (never cached, per the architecture rules).

## 2. The path, step by step
```
Pi added ──► Pi ready ──► Key ready ──► Cloned ──► Built ──► Env pushed ──► Unit installed ──► Running ──► Routed
 (0)          (1)          (2)          (3)        (4)         (5)             (6)               (7)        (8)
                                                                                         Update (9): new version → build → restart
```
Steps 2–6 are one guided flow, "New app" (⬜ as a single flow; ✅ as separate buttons). Step 9 loops back to 4–7.

| # | Step | UI / CLI | Prerequisite | Changes on the Pi | Read-only check first | Verified by | Undo | Status |
|---|---|---|---|---|---|---|---|---|
| 0 | Add the Pi | “Add a Pi…” / `host add` | SSH key access works (PI-SETUP) | nothing (PC `hosts.yaml` only) | table shows reachable | Pi row not “unreachable” | “Remove Pi…” | ✅ |
| 1 | Pi ready | “Check Pi…” / `host doctor`; “Install missing…” / `host provision`; “Install Node…” / `host install-node` | 0 | apt `git`, `curl`; `~/apps`; nvm + Node | doctor report | doctor all ✓ | apt remove / `rm -rf ~/.nvm` by hand | ✅ (uv ⬜) |
| 2 | Key ready | “Deploy key…” / `host deploy-key` | 1 (git) | `~/.ssh/raspi_deploy_<app>`, `~/.ssh/config` block, `known_hosts` | — | `git ls-remote` via the alias succeeds | delete the 2 files + config block; remove the key on GitHub | ✅ |
| 3 | Cloned | “Install app…” / `host install-app` | 2, repo declared | `git clone` into `~/apps/<app>` | key present, disk ≥ 500 MB, folder state | folder is a git working copy at a known rev | `rm -rf` the folder | ✅ |
| 4 | Built | same action (runs `build`) | 3, Node ready, `build` declared (may be empty) | `build` output in the working copy; `built: <rev>` in `apps.yaml` | — | build exit code 0 and `built` equals the working copy revision | `git clean` by hand | ✅ |
| 5 | Env pushed | “Environment…” | 3 (to find candidates and the template) | env file `chmod 600` (created if it does not exist) | diff local vs Pi, names only | sync line “same as the Pi” | restore `.raspi-bak` | ✅ |
| 6 | Unit installed | “Install unit…” / `host install-unit` | 4, `run` declared, 5 (else warning), sudo | `/etc/systemd/system/<app>.service`, enable | unit file ours/foreign/missing, node bin, env files exist | unit enabled | `systemctl disable` + delete file by hand | ✅ |
| 7 | Running | Start / Stop / Restart / Logs | 6 | systemd state | state | state `running` and logs clean | Stop | ✅ |
| 8 | Routed | “Set up route…” / `route add` | 7 (port is read from the listening socket), Cloudflare set up | tunnel + ingress + DNS (Cloudflare), connector unit on the Pi | read-only plan | tunnel healthy, HTTPS answers | “Remove route”, “Delete tunnel…” | ✅ (never run on the real account) |
| 9 | Update | “Update…” / `host update`, `host rollback` | 3, 4 (a working copy and a `repo`) | detached checkout of one commit, build, restart if it was running; `built` and `previous` recorded | fetch, then old → new revision, incoming commit subjects, dirty / local-only check | `built` equals the new revision; app running | “Update…” → go back / `host rollback` | ✅ (fake Pi; probe rehearsed read-only on `raspi-v`) |

Entry path B, **adopt an existing service** (`Scan apps…` / `host adopt`): the app enters at step 6 (unit exists, not ours,
never overwritten). Steps 2–4 are skipped; 5, 7, 8 apply as usual.

**“Next step” column** (✅, `core/stage.py`): read at each refresh from the Pi's status and the declared fields, in this
order, the first that applies wins (a hint that names the button to use, never a gate):
`Deploy key…` (repo app, nothing cloned, no key) → `Install app…` (nothing cloned) → `Build (Install app…)` (a build is
declared and `built` is not the current revision) → `Set the run command (Install app…)` → `Push environment…` (a declared
env file is missing on the Pi) → `Environment…` (no unit and no env file declared; skip it with “Install unit…” if the app
needs none) → `Install unit…` (no unit) → `Start` (stopped or failed) → `✓ Ready` (running). Adopted apps (no `repo`) only
get `Start` or `✓ Ready`. The row's tooltip gives the reason. “Routed” is not read yet.

## 3. Rules that make each step safe (apply to every step, present and future)
1. **One entry of data.** A value (app name, repo, commands, folder) is typed once and stored in `apps.yaml`; later steps
   never ask for it again. Forms for an existing app open pre-filled.
2. **Plan → confirm → apply → read back.** Read-only plan first; confirmation shows the exact script; after applying, the
   result is re-read from the Pi and the table refreshed. `--dry-run` shows the plan and script without running anything.
3. **Idempotent.** Running a step twice is safe: it does nothing, or updates, never duplicates or overwrites something
   raspi-manager did not create.
4. **Stage-aware UI.** A button is enabled only when its prerequisite holds, and a disabled button says why (tooltip).
   🟡 Done for Start (state “– no unit”, tooltip points to “Install unit…”) and “Install unit…”. The table has a “Next step” column (✅, below); making every other button follow the stage is ⬜.
   Actions on an app say what to do next when they cannot run (e.g. Start without a unit: “Create the unit first”).
5. **Silent failures are bugs.** Every worker failure ends in a visible message with the cause and the next action. No
   method name is defined twice across `MainWindow` and its mixins (test).
6. **Scripts do not read stdin.** The transport has the Pi read the whole script first (`sh -c 'eval "$(cat)"'`), so a
   command that reads stdin gets end-of-file and cannot swallow the rest of the script. ✅ Every generated script is also
   syntax-checked with `sh -n` and checked for bashisms in the tests. ✅
7. **Secrets**: never in `apps.yaml`, messages, logs or tests; env values are shown by name and count only.
8. **No change to a Pi by Claude.** Claude only runs read-only commands on a Pi; the developer runs every change.

## 4. Naming (everything derived from the app name `<app>`)
| Thing | Name |
|---|---|
| Working copy | `~/apps/<app>` (or the declared `path`) |
| Deploy key / ssh alias | `~/.ssh/raspi_deploy_<app>` / `github-<app>` → clone URL `git@github-<app>:owner/name.git` |
| App unit | `/etc/systemd/system/<app>.service`, first line `# managed by raspi-manager` |
| Env files | as declared per app (`label` + absolute path), pushed by “Environment…” |
| Tunnel / connector unit | `raspi-manager-<pi>--<app>` / `raspi-tunnel-<pi>--<app>.service` |
Adopted apps keep their own unit names; only raspi-created units follow this table.

## 5. Where things are decided or recorded
Decisions: DECISIONS.md. Behaviour: SPEC.md. Structure: ARCHITECTURE.md. This file: order, prerequisites, checks.
If code and this file disagree, say so and fix one of them in the same change.

## 6. Decisions taken on 2026-11-15 (all ⬜ unless the table above says otherwise)
1. **Built marker** (✅). After a successful build, `built: <rev>` is recorded in the app's `apps.yaml` entry (only when a
   build is declared). “Built” and “needs rebuild” are then readable from the Pi (step 4, “Next step” column).
2. **Update (step 9)** (✅). `git fetch`, then a **detached checkout of one exact commit** (branch tip, tag or sha: the same
   mechanism for an update and a rollback), build, restart if the app was running. Refused when the working copy has
   uncommitted changes to tracked files or commits that exist only on the Pi. After a successful update `built` is the new
   revision and `previous: <rev>` the one before; a rollback returns to `previous`, or to `built` after a failed update
   (the build then failed, nothing is restarted and nothing is recorded). No `releases/<sha>` layout. The fetch is the
   only write of the plan and only touches `.git` remote-tracking refs, not the working tree or the running app.
3. **“New app” wizard.** One form (name, repo, build, run, folder), asked once, then steps 2–6 one by one with a
   confirmation each and a progress list; when one fails the wizard reopens at that step. The separate buttons stay for
   special cases. The wizard is the “Install app…” entry point for a new app; for an existing app the same form is
   pre-filled (“Update an app”).
4. **Prerequisites of an app.** Optional `requires:` list in `apps.yaml` (kinds: folder exists, file exists, mount point
   present). Checked read-only by the plan of step 7 and reported by name; raspi never creates them (so nothing is written
   to the SD card by mistake). Notes App's data folder and marker file are the first use.
5. **Remove app.** “Remove app…” lists every item (inventory entry, unit, working copy, deploy key and alias, route); the
   developer ticks what goes, and confirms by typing the app name. Data folders (`requires:` paths) are never deleted.
6a. **Declaring and starting an env file** (✅). “Environment…” scans the working copy (`~/apps/<app>` when no `path`) for
   `.env*` files **and templates** (`.env.example`, `.sample`, `.template`, `.dist`), read-only. A template with no real
   file beside it proposes creating `<folder>/.env`. When that file does not exist on the Pi and nothing is saved locally,
   the editor starts from the template: defaults the project wrote are kept, names that look secret (SECRET, PASSWORD,
   PASSPHRASE, TOKEN, KEY, PRIVATE) and placeholder values (`change-me`, `<…>`, `your-…`) start empty; the editor and the
   push confirmation name the variables still without a value. Nothing is invented.
6. **Environment in the wizard.** Between steps 4 and 6 the wizard looks, read-only, for `.env.example` (and similar
   templates) in the working copy and uses its variable names to pre-fill the Environment editor (values left to fill,
   never invented); the file is then pushed from the local copy, and the unit is created only after that.

## 7. Per-step acceptance checklist (run by the developer on a real Pi before a step is called done)
- [ ] The button/command is disabled with a reason when the prerequisite is missing, enabled otherwise.
- [ ] The plan shows exactly what the confirmation lists; `--dry-run` changes nothing (compare `~/` before/after).
- [ ] Run once: expected result, table refreshed. Run twice: no duplicate, no error.
- [ ] Break the prerequisite on purpose (no key, no disk space, wrong repo): a message names the cause and the next action.
- [ ] The undo in the table above works.

## 8. Real-Pi log (what has actually been run, and what it showed)
| Date | Pi | Step | Result |
|---|---|---|---|
| 2026-11-15 | raspi-v | 1 doctor (read-only) | fit: Debian 13 aarch64, Node 26.13.1 present, 23 GB free |
| 2026-11-15 | raspi-v | 2 deploy key + verify | ✓ `SonSonDev/notes-app` readable |
| 2026-11-15 | raspi-v | 3 clone | ✓ done by the developer; **build/run left empty** (form showed grey examples) → fixed (required `run`, “Update an app”) |
| 2026-11-15 | raspi-v | transport change (whole-script read) | ✓ `host doctor` and `status` run through it; notes-app shows “– no unit” |
| 2026-11-15 | raspi-v | “Next step” column (read-only) | notes-app: cloned at 2f62548, key present, not built → “Build (Install app…)”; on `raspi`: example-bot and chat-bot “✓ Ready”, web-game “Start” |
| 2026-11-15 | raspi-v | 4–9 | not run yet |
