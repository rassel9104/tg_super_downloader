# TG Super Downloader (uso hogareño)

Bot doméstico para enviarle enlaces por Telegram y descargar archivos directamente a tu PC (Windows 11).
Soporta **HTTP/HTTPS**, **magnet:**, **.torrent**, **YouTube/streaming (yt-dlp)** y **media de Telegram (Telethon)**.
Incluye un **panel local** (FastAPI + WebSocket) para pausar/reanudar/ejecutar/cancelar/limpiar y ver progreso en tiempo real.

> ⚠️ Pensado para uso **personal** en red local. No multiusuario, no cuotas. Mantén tus credenciales y puertos privados.

---

## ✨ Características

- **Descargas desde Telegram**: reenvía un mensaje con media o un enlace y se encola automáticamente.
- **HTTP/HTTPS/magnet/.torrent** con **aria2** (JSON-RPC).
- **YouTube/otros** con **yt-dlp** (soporta playlists, canales, shorts).
- **Media de Telegram** vía **Telethon** (sesión `string` o `file`).
- **Scheduler**: ejecución diaria a la hora configurada o **ventana Start/Stop**.
- **Panel local** (127.0.0.1) con:
  - Filtros por estado y búsqueda.
  - Encolar enlaces desde la UI.
  - Progreso en tiempo real (WebSocket).
- **Cancelación segura**:
  - Aria2: remueve y borra parciales + sidecars (`.aria2`, `*.part`, `*.ytdl`).
  - yt-dlp: mata proceso activo y limpia temporales recientes.

---

## 🧱 Arquitectura

- **Bot**: `python-telegram-bot` + **Telethon** para intake de mensajes/comandos.
- **Descargas**:
  - `aria2` via RPC (`addUri`, `addTorrent`, `pauseAll`, `remove`…).
  - `yt-dlp` como subproceso cancelable (seguimiento por stdout).
  - `Telethon` para media con callback de progreso.
- **DB SQLite**: `queue`, `progress`, `kv`, `events` (con `ext_id` de aria2).
- **Panel FastAPI** (`tgdl/panel/api.py`): REST + WebSocket.
- **Control local** en `http://127.0.0.1:8765` para `/pause`, `/resume`, `/run`, `/cancel/{id}`.

---

## ✅ Requisitos

- **Windows 11**
- **Python 3.13**
- **aria2**:
  ```powershell
  choco install aria2 -y


o portable (aria2c.exe) en tools\aria2.

🚀 Instalación
# 1) Entorno virtual
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2) Dependencias
pip install --upgrade pip
pip install -r requirements.txt

Variables de entorno (.env)

Ejemplo:

# Telegram
BOT_TOKEN="123456:ABCDEF..."
API_ID=123456
API_HASH="xxxxxxxxxxxxxxxxxxxx"

# Sesión de usuario para Telethon
USE_TELETHON=true
TELETHON_SESSION_MODE="file"       # "file" o "string"
TELETHON_STRING=""                 # solo si usas mode=string
SESSIONS_DIR="./data/sessions"     # para sesiones file

# Descargas y zona horaria
DOWNLOAD_DIR="./downloads"
TIMEZONE="America/New_York"

# Scheduler
SCHEDULE_HOUR=3   # hora base (24h)
# el modo ventana Start/Stop se ajusta desde /schedule

# aria2 RPC
ARIA2_ENDPOINT="http://127.0.0.1:6800/jsonrpc"
ARIA2_SECRET="XXXXXXX"

# Panel (local)
PANEL_HOST="127.0.0.1"
PANEL_PORT=8080
PANEL_TOKEN="XXXXXXX"

# yt-dlp (opcional)
YTDLP_COOKIES="./cookies.txt"
YTDLP_PROXY=""
YTDLP_FORCE_IPV4=false
YTDLP_FORMAT="bv*+ba/b"
YTDLP_MERGE_FORMAT="mp4"
YTDLP_CONCURRENT_FRAGMENTS=4
YTDLP_HTTP_CHUNK_SIZE=1048576
YTDLP_THROTTLED_RATE=1048576
YTDLP_MAX_RUN_SECS=900
YTDLP_MAX_PLAYLIST_ITEMS=24


Consejo: no publiques .env. Crea variantes (.env.dev, .env.home) si usas varias PCs.

▶️ Ejecución
1) Inicia aria2
powershell -ExecutionPolicy Bypass -File .\scripts\run-aria2.ps1

2) Inicia el bot (incluye control local 8765)
python -m tgdl.cli bot

3) (Opcional) Inicia el panel FastAPI
powershell -ExecutionPolicy Bypass -File .\scripts\run-panel.ps1


Abre http://127.0.0.1:8080/ (UI local).

💬 Comandos del bot

/start, /help, /menu

/status, /list, /now

/pause, /resume

/cancel {id}, /clear

/retry, /purge

/schedule — activa/desactiva ventana horaria (Start/Stop)

/when HH — cambia hora base

🗂️ Descargas y carpetas

Organización automática por tipo/origen:

downloads/
  torrents/
  youtube/
  example.com/
  Telegram Canal X/

🧹 Cancelación y limpieza

Aria2: remove(gid) + borrado de parciales y sidecars (.aria2, *.part, *.ytdl) reportados por tellStatus.files[].path.

yt-dlp: termina proceso activo y limpia temporales recientes (12h por defecto).

🔐 Seguridad

Panel y control HTTP siempre en 127.0.0.1 (no WAN).

No compartas .env ni la DB.

Las cabeceras HTTP para MediaFire se pasan a aria2 como header: ["K: V"] (p. ej. Referer).

💾 Backup / Restore

DB SQLite en .\data\queue.db.
Backup con el bot apagado:

New-Item -ItemType Directory -Force -Path .\backups | Out-Null
Copy-Item .\data\queue.db .\backups\queue_$(Get-Date -Format yyyyMMdd_HHmmss).db

🛠️ Desarrollo

Iniciar aria2: .\scripts\run-aria2.ps1

Iniciar bot: python -m tgdl.cli bot

Iniciar panel: .\scripts\run-panel.ps1

Ejecutar tests:

& .\.venv\Scripts\pytest.exe -q

📁 Estructura del proyecto
tg_super_downloader/
├─ tgdl/
│  ├─ adapters/
│  │  ├─ telegram/bot_app.py
│  │  └─ downloaders/{aria2.py,ytdlp.py}
│  ├─ core/db.py
│  ├─ config/settings.py
│  └─ panel/api.py
├─ scripts/
│  ├─ run-aria2.ps1
│  ├─ run-panel.ps1
│  └─ publish.ps1
├─ downloads/        # gitignored
├─ data/             # DB y estado (gitignored)
├─ requirements.txt
├─ README.md
└─ .gitignore
