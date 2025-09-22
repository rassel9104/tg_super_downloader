$ErrorActionPreference = "Stop"
.\.venv\Scripts\Activate.ps1
$env:PYTHONPATH = (Resolve-Path ".").Path
Write-Host "🌐 Iniciando panel (FastAPI) en segundo plano..."
Start-Process powershell -ArgumentList "-NoExit","-Command",".\.venv\Scripts\Activate.ps1; uvicorn tgdl.panel.api:app --host 127.0.0.1 --port 8080"
Write-Host "🤖 (Próximas fases) Bot se iniciará con: python -m tgdl.cli bot"
