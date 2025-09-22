# Ejecuta aria2c con config local.
# Requisitos: aria2c.exe en PATH o en .\tools\aria2\aria2c.exe

$ErrorActionPreference = "Stop"

$aria2 = (Get-Command "aria2c.exe" -ErrorAction SilentlyContinue)
if (-not $aria2) {
    $candidate = Join-Path (Resolve-Path ".").Path "tools\aria2\aria2c.exe"
    if (Test-Path $candidate) { $aria2 = $candidate }
}
if (-not $aria2) {
    Write-Host "[ERR] No se encontró aria2c.exe. Instálalo con 'choco install aria2' o colócalo en .\tools\aria2\"
    exit 1
}

# Asegura carpetas usadas por la config
New-Item -ItemType Directory -Force -Path .\data | Out-Null
New-Item -ItemType Directory -Force -Path .\downloads | Out-Null

$cfg = ".\scripts\aria2-config.txt"
if (-not (Test-Path $cfg)) {
    Write-Host "[ERR] No existe $cfg"
    exit 1
}

# Si definiste ARIA2_SECRET en .env y quieres forzarla aquí, puedes generar una copia temporal:
# (opcional, normalmente basta con que el cliente use token)
if (-not (Test-Path ".\data\aria2.session")) {
    New-Item -ItemType File -Path .\data\aria2.session | Out-Null
}

# $env:ARIA2_SECRET | Out-Null

Write-Host "[OK] Iniciando aria2c con $cfg"
& $aria2 --conf-path="$cfg"
