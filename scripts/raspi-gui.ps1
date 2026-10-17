<#
.SYNOPSIS
  Open the raspi dashboard in its own window (Edge "app mode", no address bar).

.DESCRIPTION
  Starts `raspi web` in the background if nothing listens on the port yet, opens the
  dashboard in a dedicated Edge window, and stops the server again when that window
  is closed. If a server was already running, it is left alone.

  -InstallShortcut creates a Desktop shortcut "raspi" that runs this script.
#>
param(
    [int]$Port = 8765,
    [switch]$InstallShortcut
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$url = "http://127.0.0.1:$Port/"
$data = Join-Path $env:LOCALAPPDATA 'raspi'
New-Item -ItemType Directory -Force $data | Out-Null

if ($InstallShortcut) {
    $lnk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'raspi.lnk'
    $sh = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
    $sh.TargetPath = (Get-Command powershell.exe).Source
    $sh.Arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    $sh.WorkingDirectory = $root
    $sh.Description = 'raspi dashboard'
    $sh.Save()
    Write-Host "Shortcut created: $lnk"
    return
}

function Test-Port([int]$p) {
    try {
        $c = New-Object Net.Sockets.TcpClient
        $c.Connect('127.0.0.1', $p)
        $c.Close()
        return $true
    } catch { return $false }
}

function Show-Error([string]$msg) {
    Add-Type -AssemblyName System.Windows.Forms
    [void][System.Windows.Forms.MessageBox]::Show($msg, 'raspi', 'OK', 'Error')
}

$server = $null
if (-not (Test-Port $Port)) {
    $log = Join-Path $data 'server.log'
    $server = Start-Process -FilePath python -WorkingDirectory $root -WindowStyle Hidden -PassThru `
        -ArgumentList '-m', 'uv', 'run', 'raspi', 'web', '--port', $Port `
        -RedirectStandardError $log -RedirectStandardOutput (Join-Path $data 'server.out.log')
    $deadline = (Get-Date).AddSeconds(30)
    while (-not (Test-Port $Port)) {
        if ($server.HasExited -or (Get-Date) -gt $deadline) {
            Show-Error "The raspi server did not start. See $log"
            exit 1
        }
        Start-Sleep -Milliseconds 300
    }
}

$edge = Get-Command msedge.exe -ErrorAction SilentlyContinue
if (-not $edge) {
    $candidate = "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
    if (Test-Path $candidate) { $edge = Get-Item $candidate }
}
if ($edge) {
    $exe = if ($edge.PSObject.Properties['Source']) { $edge.Source } else { $edge.FullName }
    # Own profile: the window runs as its own process, so we can wait for it to close.
    $window = Start-Process -FilePath $exe -PassThru -ArgumentList @(
        "--app=$url", "--user-data-dir=$(Join-Path $data 'edge-profile')",
        '--no-first-run', '--no-default-browser-check', '--window-size=1100,800')
    $window.WaitForExit()
} else {
    Start-Process $url   # default browser; we cannot tell when it is closed
    $server = $null      # leave the server running
}

if ($server -and -not $server.HasExited) {
    & taskkill.exe /PID $server.Id /T /F | Out-Null   # uv + python child
}
