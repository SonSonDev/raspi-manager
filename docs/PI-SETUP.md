# Preparing a Raspberry Pi for raspi (SSH key access)

Goal: from this Windows PC, `ssh <user>@<pi>` works **without any prompt**, so `raspi` (and Claude) can run commands non-interactively. No password is ever stored.

## Quick path
1. Flash Raspberry Pi OS Lite 64-bit with Raspberry Pi Imager; in its settings set hostname, user, Wi-Fi/Ethernet and **enable SSH** (password auth is fine for this first step). Boot the Pi.
2. Find it: `raspi.local`, the router's device list, or `arp -a` after a ping sweep.
3. Run, from the repo root, in a normal PowerShell:
   ```
   .\scripts\setup-pi-ssh.ps1 -PiHost raspi.local -User <user>
   ```
   Use `-CheckOnly` to verify without changing anything.
4. All lines must show `[ OK ]`. The script ends with the Pi's hostname, CPU architecture, OS, Python and git versions, and the linger state.

If the script runs under execution-policy restrictions: `powershell -ExecutionPolicy Bypass -File .\scripts\setup-pi-ssh.ps1 ...`.

## What the script does
| Step | Check / action | If it fails |
|---|---|---|
| 1 | `ssh`, `ssh-keygen`, `ssh-add`, `scp` exist | Settings > Apps > Optional features > **OpenSSH Client** |
| 2 | Key `~/.ssh/id_ed25519` exists, else creates it (you choose a **passphrase**: recommended) | — |
| 3 | `ssh-agent` service is **Automatic** and **Running**; your key is **loaded** in it (`ssh-add -l`) | Needs an **admin** PowerShell for the service (the script prints the exact command, it never self-elevates); the key load asks your passphrase once |
| 4 | Public key installed on the Pi (skipped if already accepted); idempotent, no duplicates | Asks the Pi password once or twice; wrong host/user shows an scp error |
| 5 | Login in `BatchMode` (forbids any prompt) + facts about the Pi | Usually step 3 (key not in agent) or 4 |

Why the agent matters: a key with a passphrase can only be used silently if it is loaded in `ssh-agent`. `BatchMode` tests exactly that. Loading survives reboots with the Windows agent (it persists keys in the registry), but re-run `-CheckOnly` after a PC reinstall.

## Manual equivalent (what the script automates)
```
# admin PowerShell, once per PC
Set-Service ssh-agent -StartupType Automatic
Start-Service ssh-agent
# normal PowerShell
ssh-keygen -t ed25519 -C "raspi-pc"            # once per PC, with a passphrase
ssh-add $env:USERPROFILE\.ssh\id_ed25519       # once per boot of the agent
scp $env:USERPROFILE\.ssh\id_ed25519.pub <user>@<pi>:/tmp/pc.pub
ssh <user>@<pi> "mkdir -p ~/.ssh && cat /tmp/pc.pub >> ~/.ssh/authorized_keys && rm /tmp/pc.pub && chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys"
ssh -o BatchMode=yes <user>@<pi> true          # must print nothing and exit 0
```
Pitfalls seen in practice:
- Run these on the **PC**, not inside an SSH session on a Pi (prompt `user@raspi:~$` means you are on the Pi).
- The `!` prefix in Claude Code runs **Git Bash**, not PowerShell: `$env:USERPROFILE` and `type` do not work there.
- Do not pipe the key with `cat`/`type` in PowerShell: line-ending changes can corrupt `authorized_keys`; use `scp`.
- "Enter passphrase for key" during scp/ssh means the Pi already accepts the key; only the agent is missing.
- The "not using a post-quantum key exchange" warning is informational (older server).

## Trust the Pi's host key (first connection)
`ssh` refuses unknown hosts (`Host key verification failed`) and raspi never accepts keys for you. Once per Pi, from a normal PowerShell:
```
ssh <user>@<pi>        # shows "SHA256:..." and asks "yes/no"; answer yes, then exit
```
Before answering `yes`, compare the fingerprint with the one on the Pi itself (screen/keyboard or an already-trusted session): `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`. If the Pi is reinstalled and the key changes: `ssh-keygen -R <pi>` on the PC, then connect again and re-check the fingerprint. (`setup-pi-ssh.ps1` does this connection as part of its steps.)

## Register the Pi in raspi-manager
In the app: **Add a Pi…** (Ctrl+N: name, address, login user), or in a terminal `raspi host add NAME ADDRESS USER`. Both write `~/.raspi/hosts.yaml` (`%USERPROFILE%\.raspi\hosts.yaml`) and send nothing to the Pi. Name = letters/digits/`.`/`_`/`-` (it names env copies and tunnels, so it cannot be changed later), address = hostname or IP, user = lowercase login. Edit address/user with **Edit Pi…** or `raspi host edit`, forget a Pi with **Remove Pi…** or `raspi host rm`.

The file can also be written by hand:
```yaml
hosts:
- name: raspi-v
  address: raspi-v.local
  user: pi
  apps:                         # optional until the adopt scan exists
  - name: web-game
    unit: web-game.service      # systemd unit on the Pi
    path: /home/pi/web-game # optional: git working copy, gives the version
```
Check with `raspi host ls`, then open the app: the table shows each Pi's apps, or an "unreachable" row if the key or host key is not accepted yet. Env files are declared from the app (Environment…), which rewrites this file and keeps `hosts.yaml.bak`.

## After the key works (optional hardening)
Once passwordless login is verified, you may disable password login on the Pi (`PasswordAuthentication no` in `/etc/ssh/sshd_config.d/`, then restart `ssh`). Keep a second open session while testing so you cannot lock yourself out. `raspi` never uses passwords.

## Known Pis
Record hosts in `~/.raspi/hosts.yaml` (see above), not in this repo. Detected on the LAN on 2026-10-17: `raspi.local` (192.168.1.20, user `pi`) and `raspi-v.local` (192.168.1.21) — SSH open on both, second one not yet set up.
