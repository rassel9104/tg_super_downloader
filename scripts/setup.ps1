# setup.ps1 - Inicialización de entorno
# Uso: powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1

$ErrorActionPreference = "Stop"

if (-not (Test-Path ".venv")) {
    py -3.13 -m venv .venv
}

.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\pip install -e ".[dev]"

# Instalar pre-commit si hay repo git
if (Test-Path ".\.git") {
    .\.venv\Scripts\python -m pip install pre-commit
    .\.venv\Scripts\pre-commit install
}

Write-Host "[OK] Entorno listo. Activa el venv con: .\.venv\Scripts\Activate.ps1"
