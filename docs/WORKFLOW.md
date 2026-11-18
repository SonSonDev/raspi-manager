# Workflow — from a bare Pi to an app online and kept up to date
Single reference for the end-to-end path. SPEC says *what each feature does*; this file says *in which order, with which
prerequisites, and how each step is checked*. **Any new step or button is written here first** (rule at the bottom).
Status marks: ✅ built · 🟡 partly · ⬜ not built. Everything marked ✅ was tested with a fake Pi unless noted; the first
real run on `raspi-v` is listed in "Real-Pi log".

## 1. Vocabulary
- **Pi**: a machine in `~/.raspi/hosts.yaml` (name, address, login user). **App**: an entry in the Pi's `~/.raspi/apps.yaml`.
- **Declared fields** of an app (all in the Pi's `apps.yaml`, never secrets): `name`, `unit`, `repo` (`owner/name`),
  `build`, `run`, `path`, `env_files` (⬜ planned: `processes`, see §6b). They are asked **once**, in one form, and every later step reads them from there.
- **Stage**: how far an app has come. Always *read from the Pi* (never cached, per the architecture rules).

## 2. The path, step by step
```
Pi added ──► Pi ready ──► Key ready ──► Cloned ──► Built ──► Env pushed ──► Unit installed ──► Running ──► Routed
 (0)          (1)          (2)          (3)        (4)         (5)             (6)               (7)        (8)
                                                                                         Update (9): new version → build → restart
```
Steps 2–6 and the start are one guided flow, **App → New app…** (✅), which can be closed and reopened with **App → Continue setup…** where it stopped; each step is also a menu item of its own for special cases. Step 9 loops back to 4–7.

| # | Step | UI / CLI | Prerequisite | Changes on the Pi | Read-only check first | Verified by | Undo | Status |
|---|---|---|---|---|---|---|---|---|
| 0 | Add the Pi | “Add a Pi…” / `host add` | SSH key access works (PI-SETUP) | nothing (PC `hosts.yaml` only) | table shows reachable | Pi row not “unreachable” | “Remove Pi…” | ✅ |
| 1 | Pi ready | “Check Pi…” / `host doctor`; “Install missing…” / `host provision`; “Install Node…” / `host install-node`; **Pi → Install tools…** / `host install-tools` (ffmpeg through apt, yt-dlp from the official release; for apps that need them) | 0 | apt `git`, `curl`; `~/apps`; nvm + Node | doctor report | doctor all ✓ | apt remove / `rm -rf ~/.nvm` by hand | ✅ (uv ⬜) |
| 2 | Key ready | “Deploy key…” / `host deploy-key`; afterwards **Pi → Manage deploy keys…** / `host deploy-keys [--check]` (list, check access), `host deploy-key-rotate`, `host deploy-key-rm` | 1 (git) | `~/.ssh/raspi_deploy_<app>`, `~/.ssh/config` block, `known_hosts` | — | `git ls-remote` via the alias succeeds | “Manage deploy keys… → Delete…” / `host deploy-key-rm` (2 files + config block); remove the key on GitHub by hand | ✅ |
| 3 | Cloned | “Install app…” / `host install-app` | 2, repo declared | `git clone` into `~/apps/<app>` | key present, disk ≥ 500 MB, folder state | folder is a git working copy at a known rev | `rm -rf` the folder | ✅ |
| 4 | Built | same action (runs `build`) | 3, Node ready, `build` declared (may be empty) | `build` output in the working copy; `built: <rev>` in `apps.yaml` | — | build exit code 0 and `built` equals the working copy revision | `git clean` by hand | ✅ |
| 5 | Env pushed | “Environment…” | 3 (to find candidates and the template) | env file `chmod 600` (created if it does not exist) | diff local vs Pi, names only | sync line “same as the Pi” | restore `.raspi-bak` | ✅ |
| 6 | Unit installed | “Install unit…” / `host install-unit` | 4, `run` declared, 5 (else warning), sudo | `/etc/systemd/system/<app>.service`, enable | unit file ours/foreign/missing, node bin, env files exist | unit enabled | `systemctl disable` + delete file by hand | ✅ |
| 7 | Running | Start / Stop / Restart / Logs | 6 | systemd state | state | state `running` and logs clean | Stop | ✅ |
| 8 | Routed | “Set up route…” / `route add` (also repairs a connector whose tunnel was deleted: token replaced, unit restarted). **Run once per hostname**: an app may have several routes (hostname → port, optional check path), all in the app's one tunnel; each success is recorded in its `routes:` entry | 7 (port is read from the listening socket), Cloudflare set up | tunnel + ingress + DNS (Cloudflare), connector unit on the Pi (only for the first route), `routes:` in `apps.yaml` | read-only plan; duplicate hostname refused, shared port warned | tunnel healthy, HTTPS answers; then every status reads each route's local check (“main up, share down”) | “Remove route”, “Delete tunnel…” | ✅ (never run on the real account) |
| — | Remove | “Remove app…” / `host remove-app` | the app is in the inventory | what is ticked, see §6 point 5 | the plan lists what exists and what is refused | the app disappears from the table | add it again with “New app…” | ✅ |
| 9 | Update | “Update…” / `host update`, `host rollback` | 3, 4 (a working copy and a `repo`) | detached checkout of one commit, build, restart if it was running; `built` and `previous` recorded | fetch, then old → new revision, incoming commit subjects, dirty / local-only check | `built` equals the new revision; app running | “Update…” → go back / `host rollback` | ✅ (fake Pi; probe rehearsed read-only on `raspi-v`) |
| 10 | Moved | **App → Move to another device…** / `host move-app SOURCE APP TARGET` | the app set up on TARGET at the same revision (steps 2–7 without starting it), TARGET not running it | stop on SOURCE; each `requires: folder` copied as tar through this PC into an empty folder of TARGET; env files copied; unit started on TARGET; unit disabled on SOURCE | read-only probe of both: unit state, revision, CPU, Node major, sudo, free disk, mounts, data folder state and size, env folders | SHA-256 of every file equal on both sides; unit active on TARGET | SOURCE keeps its data: enable and start its unit again, stop TARGET's | ✅ (fake Pi; scripts run for real in a throw-away home; probe not yet run on a real Pi) |

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
2b. **One change at a time per Pi** (✅). While an install (packages, Node, tools) runs on a Pi, a second one is refused with a
   message instead of failing on the Pi's lock; every `apt-get` call also waits for another apt run (a first install, the daily
   timers) with `-o DPkg::Lock::Timeout=600` instead of failing.
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
3. **“New app” wizard** (✅, fake Pi). One form (name, repo, build, run, folder) asked once, then a window with five steps (deploy
   key, clone and build, environment, unit, start), each with its own confirmation showing the exact script; only the first
   step not done is actionable. Done-ness is re-read from the Pi before and after every step, so a failed or abandoned step
   leaves nothing half-recorded and the wizard reopens at that step (**Continue setup…** on the selected app). The key step
   is done only once GitHub accepted the key in this session (or the repo is cloned); a key GitHub refuses shows the public
   key to add; the environment step opens the existing editor (template pre-fill, 6a) or “the app needs no environment
   file” (kept for the session only); the wizard ends by suggesting Cloudflare → Set up route…. The separate menu items stay.
4. **Prerequisites of an app** (✅, fake Pi; the check rehearsed read-only on `raspi-v`). Optional `requires:` list in `apps.yaml`,
   entries `{kind, path}` with kind `folder`, `file` or `mount` (a real mount point), absolute paths. They are read with every
   status (read-only `[ -d ]`, `[ -f ]`, `mountpoint -q`) and reported by name; raspi never creates them (so nothing is
   written to the SD card by mistake). A missing one makes the “Next step” column say “Prerequisite missing” (before Start),
   the wizard's start step lists it, and a start asks “Start anyway?” first. They are typed in the install form (one per line,
   `mount /mnt/data`) or with `--requires mount:/mnt/data`. Notes App's data folder and marker file are the first use.
5. **Remove app** (✅, fake Pi plus a real run of the script in a throw-away home; the plan rehearsed read-only on `raspi-v`).
   **App → Remove app…** / `host remove-app NAME APP [--workdir] [--key] [--env] [--local-env] [--keep-inventory] [--keep-unit]`
   lists six items: the list entry (apps.yaml) and the unit are ticked at first; the working copy, the deploy key and its ssh
   alias, the env file(s) on the Pi and the local env copies on the PC are offered unticked. An item that cannot be removed is
   greyed with the reason (a unit not made by raspi-manager, a folder that is not a git working copy, a working copy that holds a
   `requires:` path, a path outside the home folder, an app declared in hosts.yaml). The exact script is shown and updates
   with the ticks; **Remove** needs the app name typed and at least one tick. The Pi side runs first, the list entry last: a
   failure leaves the app listed. Never touched: the `requires:` folders (data), anything outside home, and the Cloudflare
   route and tunnel (removed from the Cloudflare window).
6a. **Declaring and starting an env file** (✅). “Environment…” scans the working copy (`~/apps/<app>` when no `path`) for
   `.env*` files **and templates** (`.env.example`, `.sample`, `.template`, `.dist`), read-only. A template with no real
   file beside it proposes creating `<folder>/.env`. When that file does not exist on the Pi and nothing is saved locally,
   the editor starts from the template: defaults the project wrote are kept, names that look secret (SECRET, PASSWORD,
   PASSPHRASE, TOKEN, KEY, PRIVATE) and placeholder values (`change-me`, `<…>`, `your-…`) start empty; the editor and the
   push confirmation name the variables still without a value. Nothing is invented. **Reset from template…** (in the editor)
   replaces the editor's variables by the template's after a confirmation (same rules); nothing is written to the Pi until
   the usual push, and “Import from Pi…” brings the current file back.
6. **Environment in the wizard.** Between steps 4 and 6 the wizard looks, read-only, for `.env.example` (and similar
   templates) in the working copy and uses its variable names to pre-fill the Environment editor (values left to fill,
   never invented); the file is then pushed from the local copy, and the unit is created only after that.

7a. **Move an app to another Pi** (✅, fake Pi). Refusals (nothing is touched): same Pi, app not in TARGET's inventory, no
   unit or no working copy on TARGET, TARGET at another revision, TARGET already running the app, CPU or Node major differ,
   no passwordless sudo, a `mount` prerequisite not mounted, a data folder on TARGET that is not empty (never overwritten) or
   whose parent is missing (raspi creates only the last folder, never the path), an env file's folder missing, more than
   512 MB of data (held in memory on the PC), too little disk (twice the data + 500 MB). The source is stopped first and only
   if it was running; a stopped source copies the data and starts nothing. Any failure before the target starts restarts the
   source (when it was running) and empties the folders just written on TARGET. A checksum difference aborts. The source
   unit is disabled only after the target is active; **the source data is never deleted** (use “Remove app…” later, when
   decided). Env files travel as text through memory, are never shown or saved on the PC, and replace TARGET's with a
   `.raspi-bak`. Not done: an app-provided check command (e.g. `PRAGMA integrity_check`), the Cloudflare route (re-run
   “Set up route…” on TARGET), setting TARGET up in the same flow, inventory change on SOURCE.
7. **Several routes per app** (⬜, first lot of Silent Fill's requests). `apps.yaml` entry gets an optional `routes:` list,
   `{hostname, port, check}` (`check`: absolute URL path, default `/`). “Set up route…” is run once per hostname; the first
   run creates the tunnel and the connector, the next ones only add an ingress rule and a DNS record (same tunnel
   `raspi-manager-<pi>--<app>`). A successful run records the route in the Pi's `apps.yaml` (no `apps.yaml`: hosts.yaml).
   Validation: the same hostname twice in one app is refused when the file is read and when a route is planned; the same
   hostname on another app, or the same port on another app of that Pi, is a warning in the plan, not a refusal.
   **Route check:** every status also runs, read-only on the Pi, `curl` against `http://127.0.0.1:<port><check>` for each
   declared route and keeps the HTTP code (0: nothing answered). The Next-step tooltip and the app row's tooltip say
   “main up, share down” per route, by hostname and port only (never the response body). A route whose port does not
   answer after a start is a warning, not a block. Picking a check path that is cheap and has no secrets (e.g. `/robots.txt`)
   is up to the app; raspi never prints a response body or a query string.

## 6b. Several processes per app, and the unit kept in step (⬜ planned, decided 2026-11-18)
Asked for an app whose collector must not restart on every update while its web server must. Nothing here is built yet.
1. **Declare** (🟡 the `apps.yaml` format, its validation and round trip are built; the GUI form, `--process` and Edit processes… are ⬜): one list `processes:` of `{name, run, on_update}` in the app's `apps.yaml` entry. GUI: the “Install app…”
   form has a “Processes” list (name, run, update rule; “Add process”) in place of the single run field, and **App → Edit
   processes…** reopens it pre-filled. CLI: `install-app … --process NAME:COMMAND:RULE` (repeatable; `--run` stays and
   makes the process `main`). `name`: lowercase letters, digits, `-`, unique, at most 8 per app; `run` validated like today's and required; `unit` optional
   (default `<app>-<name>.service`; an adopted unit keeps its name); the app's own `unit` and `run` are those of the first process.
2. **Units** (🟡 writing one unit per process is built, fake Pi; removing the unit of a process taken off the list is ⬜): “Install unit…” / `install-unit` writes one unit per process, `<app>-<name>.service` (a lone `main`
   keeps `<app>.service`), same user, working copy and env files, marked `# managed by raspi-manager`; one confirmation
   shows every file. A process removed from the list gets its unit stopped, disabled and deleted, only if ours.
3. **Update rule** `on_update` (⬜): `restart` (default: restarted when it was running), `keep` (never restarted by an
   update or a rollback; it runs the old code until restarted by hand), `ask` (a tick box in the Update confirmation, ticked
   by default). A stopped process stays stopped. The plan lists each process and what happens to it; the build runs once.
4. **Restart one process** (⬜): Start, Stop, Restart and Logs act on the selected process; “Restart now” on a kept one.
5. **Table** (⬜): one row per process; a process started before the last build or on another revision than the working
   copy shows “old code” in Next step, with the reason in the tooltip.
6. **Existing unit** (⬜): a unit already running as `<app>.service` is adopted as the process `main` (or the name given),
   keeping its unit name; nothing is restarted by the migration.
7. **Unit drift** (✅ fake Pi; the detection's read-only lines also run in a throw-away shell): at each refresh, read-only, the unit a process should have (from `apps.yaml`) is compared with the
   file on the Pi, for units carrying our marker only. A difference shows “Unit out of date” in Next step (the tooltip names
   the field: command, env files, path). “Install app…” that changes `run`, `env_files` or `processes` on an app whose unit
   is installed adds a ticked box “Also rewrite the unit …” to its confirmation (GUI; CLI `--update-unit/--keep-unit`, default update), with the unit file in the details. Offered only when the unit is ours and `run`, `path` or env files changed for a known app. Writing a unit never restarts
   the process; the confirmation says the new command applies at the next start, and `on_update` is applied as above.
   Compared: run command, environment files, working folder (names only, never values); not Node's folder. Not shown for a
   foreign unit or an app without `run`. Origin: a changed run command had no effect until “Install unit…” was run again, and nothing said so.
8. **Risks to cover in tests and warnings**: a rollback or build replaces files under a kept process; a schema change
   under a kept process that shares a database (open question in QUESTIONS.md).
Undo: put the old list back with “Edit processes…” and apply; units are rewritten or removed accordingly.

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
| 2026-11-15 | raspi-v | wizard steps read for notes-app (read-only) | all five steps ✓: key used by the clone, built, 1 env file on the Pi, unit exists, running |
| 2026-11-15 | raspi-v | “Remove app…” plan for notes-app (read-only; nothing removed) | six items listed: list entry and unit ticked, working copy, key, env file and local copies offered; no prerequisites declared |
| 2026-11-15 | raspi-v | prerequisite checks (read-only rehearsal) | folder, file and the root mount point ✓; a plain folder checked as “mount” ✗ and a missing mount ✗, as expected |
| 2026-11-15 | raspi-v | 4–9 | notes-app was taken through them by the developer by hand; the wizard itself has not been run on a Pi yet |
