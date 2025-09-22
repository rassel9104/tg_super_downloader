$ErrorActionPreference = "Stop"
.\.venv\Scripts\Activate.ps1
uvicorn tgdl.panel.api:app --host 127.0.0.1 --port 8080 --reload
