<#
.SYNOPSIS
  Create the Desktop shortcut "raspi" that opens the native app (no console window).
  Run `python -m uv sync` first so .venv exists.
#>
$root = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $root '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $pythonw)) { throw "Missing $pythonw. Run: python -m uv sync" }

$lnk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'raspi.lnk'
$sh = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
$sh.TargetPath = $pythonw
$sh.Arguments = '-m raspi.gui'
$sh.WorkingDirectory = $root
$sh.Description = 'raspi - manage your Raspberry Pi'
$sh.Save()
Write-Host "Shortcut created: $lnk"
