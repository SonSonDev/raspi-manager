<#
.SYNOPSIS
  Create the Desktop shortcut "raspi-manager" that opens the native app (no console window).
  Run `python -m uv sync` first so .venv exists.
#>
$root = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $root '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $pythonw)) { throw "Missing $pythonw. Run: python -m uv sync" }

$desktop = [Environment]::GetFolderPath('Desktop')
$old = Join-Path $desktop 'raspi.lnk'
if (Test-Path $old) { Remove-Item $old; Write-Host "Removed old shortcut: $old" }
$lnk = Join-Path $desktop 'raspi-manager.lnk'
$sh = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
$sh.TargetPath = $pythonw
$sh.Arguments = '-m raspi.gui'
$sh.WorkingDirectory = $root
$sh.Description = 'raspi-manager - manage your Raspberry Pi'
$sh.IconLocation = (Join-Path $root 'src\raspi\gui\assets\raspi-manager.ico') + ',0'
$sh.Save()
Write-Host "Shortcut created: $lnk"
