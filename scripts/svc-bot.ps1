# svc-bot.ps1
$ErrorActionPreference = 'Stop'

$root = "C:\tools\tg_super_downloaderok"
$py   = Join-Path $root ".venv\Scripts\python.exe"

# Entorno “seguro” para servicios (Session 0)
$env:USERPROFILE = Join-Path $root "svc\home"
$env:HOME        = $env:USERPROFILE
$env:TEMP        = Join-Path $root "svc\tmp"
$env:TMP         = $env:TEMP
$env:PYTHONUNBUFFERED = "1"

# Asegurar carpetas
$null = New-Item -Force -ItemType Directory $env:USERPROFILE, $env:TEMP | Out-Null

# IMPORTANTe: NO tocar .env aquí; deja que la app lo procese como en modo manual
Set-Location $root
& $py -m tgdl.cli bot
