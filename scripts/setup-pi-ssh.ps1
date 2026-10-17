<#
.SYNOPSIS
  Prepare SSH key access from this Windows PC to a (new) Raspberry Pi and verify it.

.DESCRIPTION
  1. checks the OpenSSH client tools
  2. checks / creates the PC key pair
  3. checks the ssh-agent service (running + automatic) and that the key is loaded in it
  4. installs the public key on the Pi (idempotent) -- asks the Pi password once, only if needed
  5. verifies passwordless login (BatchMode) and prints basic facts about the Pi

  Never stores or reads passwords or passphrases: ssh/ssh-add prompt you directly.
  Does not elevate itself: if the agent service needs admin rights, it tells you what to run.

.EXAMPLE
  .\scripts\setup-pi-ssh.ps1 -PiHost raspi.local -User pi
  .\scripts\setup-pi-ssh.ps1 -PiHost 192.168.1.20 -User pi -CheckOnly
#>
param(
    [Parameter(Mandatory)][string]$PiHost,
    [Parameter(Mandatory)][string]$User,
    [string]$KeyPath = (Join-Path $env:USERPROFILE '.ssh\id_ed25519'),
    [switch]$CheckOnly   # verify only, change nothing
)

$ErrorActionPreference = 'Stop'
$script:failed = 0

function Ok($m)   { Write-Host "[ OK ] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "[WARN] $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "[FAIL] $m" -ForegroundColor Red; $script:failed++ }
function Step($m) { Write-Host "`n== $m" -ForegroundColor Cyan }

function Test-Admin {
    $p = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

$pubPath = "$KeyPath.pub"
$target  = "$User@$PiHost"
$sshOpts = @('-o', 'StrictHostKeyChecking=accept-new', '-o', 'ConnectTimeout=8')

# --- 1. tools -------------------------------------------------------------
Step '1. OpenSSH client tools'
foreach ($t in 'ssh', 'ssh-keygen', 'ssh-add', 'scp') {
    if (Get-Command $t -ErrorAction SilentlyContinue) { Ok $t } else { Fail "$t not found (Settings > Optional features > OpenSSH Client)" }
}
if ($script:failed) { exit 1 }

# --- 2. key pair ----------------------------------------------------------
Step '2. Key pair'
if (Test-Path $pubPath) {
    Ok "found $pubPath"
} elseif ($CheckOnly) {
    Fail "no key at $KeyPath (run without -CheckOnly to create one)"; exit 1
} else {
    Write-Host "No key at $KeyPath. Creating an ed25519 key; choose a passphrase when asked."
    ssh-keygen -t ed25519 -C "raspi-pc" -f $KeyPath
    if ($LASTEXITCODE -ne 0) { Fail 'ssh-keygen failed'; exit 1 }
    Ok 'key created'
}
$fingerprint = ((ssh-keygen -lf $pubPath) -split '\s+')[1]
Ok "fingerprint $fingerprint"

# --- 3. ssh-agent ---------------------------------------------------------
Step '3. ssh-agent'
$svc = Get-Service ssh-agent -ErrorAction SilentlyContinue
if (-not $svc) {
    Fail 'ssh-agent service missing: install the OpenSSH Client optional feature'
} else {
    if ($svc.StartType -eq 'Automatic') { Ok 'start type: Automatic' }
    elseif (-not $CheckOnly -and (Test-Admin)) { Set-Service ssh-agent -StartupType Automatic; Ok 'start type set to Automatic' }
    else { Fail 'start type is not Automatic. Run in an ADMIN PowerShell: Set-Service ssh-agent -StartupType Automatic' }

    if ($svc.Status -eq 'Running') { Ok 'service running' }
    elseif (-not $CheckOnly -and (Test-Admin)) { Start-Service ssh-agent; Ok 'service started' }
    else { Fail 'service not running. Run in an ADMIN PowerShell: Start-Service ssh-agent' }
}

$loaded = (ssh-add -l 2>&1) -join "`n"
if ($loaded -match [regex]::Escape($fingerprint)) {
    Ok 'key is loaded in the agent'
} elseif ($CheckOnly) {
    Fail "key not loaded. Run: ssh-add `"$KeyPath`""
} elseif ((Get-Service ssh-agent -ErrorAction SilentlyContinue).Status -eq 'Running') {
    Write-Host 'Loading the key into the agent; type your passphrase.'
    ssh-add $KeyPath
    if ($LASTEXITCODE -eq 0) { Ok 'key loaded' } else { Fail 'ssh-add failed' }
} else {
    Fail 'cannot load the key: agent is not running'
}
if ($script:failed) { Write-Host "`nFix the items above, then re-run." -ForegroundColor Red; exit 1 }

# --- 4. install the public key on the Pi ----------------------------------
Step "4. Public key on $target"
ssh @sshOpts -o BatchMode=yes $target true 2>$null
if ($LASTEXITCODE -eq 0) {
    Ok 'key already accepted by the Pi'
} elseif ($CheckOnly) {
    Fail 'Pi does not accept the key yet (run without -CheckOnly)'; exit 1
} else {
    Write-Host "The Pi will ask for the password of '$User' (once per command)."
    # scp + remote append: avoids PowerShell pipe line-ending issues; idempotent via grep.
    scp @sshOpts $pubPath "${target}:/tmp/pc.pub"
    if ($LASTEXITCODE -ne 0) { Fail 'scp failed (wrong host/user/password?)'; exit 1 }
    $remote = 'mkdir -p ~/.ssh && chmod 700 ~/.ssh && touch ~/.ssh/authorized_keys && (grep -qxFf /tmp/pc.pub ~/.ssh/authorized_keys || cat /tmp/pc.pub >> ~/.ssh/authorized_keys) && rm -f /tmp/pc.pub && chmod 600 ~/.ssh/authorized_keys'
    ssh @sshOpts $target $remote
    if ($LASTEXITCODE -ne 0) { Fail 'remote install failed'; exit 1 }
    Ok 'key installed'
}

# --- 5. verify + facts ----------------------------------------------------
Step '5. Passwordless login check'
$facts = 'hostname; uname -m; . /etc/os-release; echo $PRETTY_NAME; python3 --version; git --version; loginctl show-user $USER -p Linger'
$out = ssh @sshOpts -o BatchMode=yes $target $facts 2>&1
if ($LASTEXITCODE -eq 0) {
    Ok 'passwordless login works (no passphrase or password prompt)'
    $out | ForEach-Object { Write-Host "       $_" }
    if (($out -join ' ') -match 'Linger=no') { Warn "linger is off: services stop when '$User' logs out (raspi host provision will enable it)" }
} else {
    Fail 'BatchMode login failed. Most likely the key is not loaded in ssh-agent (step 3) or not installed (step 4).'
    $out | ForEach-Object { Write-Host "       $_" }
}

if ($script:failed) { exit 1 }
Write-Host "`nAll good: $target is ready for raspi." -ForegroundColor Green
