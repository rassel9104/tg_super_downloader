# svc-panel.ps1
$ErrorActionPreference = 'Stop'

$root = "C:\tools\tg_super_downloaderok"
$py   = Join-Path $root ".venv\Scripts\python.exe"

$env:USERPROFILE = Join-Path $root "svc\home"
$env:HOME        = $env:USERPROFILE
$env:TEMP        = Join-Path $root "svc\tmp"
$env:TMP         = $env:TEMP
$env:PYTHONUNBUFFERED = "1"

$null = New-Item -Force -ItemType Directory $env:USERPROFILE, $env:TEMP | Out-Null

# NO cargar .env en el runner
Set-Location $root
& $py -m tgdl.cli panel
