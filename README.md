# TG Super Downloader (uso hogareño)

Bot doméstico para enviarle enlaces por Telegram y descargar archivos directamente a tu PC (Windows 11).
Soporta **HTTP/HTTPS**, **magnet:**, **.torrent**, **YouTube/streaming (yt-dlp)** y **media de Telegram (Telethon)**.
Incluye un **panel local** (FastAPI + WebSocket) para pausar/reanudar/ejecutar/cancelar/limpiar y ver progreso en tiempo real.

> ⚠️ Pensado para uso **personal** en red local. No multiusuario, no cuotas. Mantén tus credenciales y puertos privados.

---

## ✨ Características

- **Descargas desde Telegram**: reenvía un mensaje con media o un enlace y se encola automáticamente.
- **HTTP/HTTPS** con **aria2** (JSON-RPC).
- **Magnet y .torrent**:
  - Magnet → lo toma aria2.
  - `.torrent` (URL o archivo reenviado) → se pasa a aria2 con `aria2.addTorrent`.
- **YouTube/otros** con **yt-dlp** (módulo o binario).
  - Soporta playlists, canales, shorts.
- **Media de Telegram** vía **Telethon** (sesión `string` o `file`).
- **Scheduler**: ejecución diaria a la hora configurada o **ventana Start/Stop**.
- **Panel local** (opcional) en `http://127.0.0.1:8080/` con:
  - Filtros por estado y búsqueda.
  - Encolar enlaces desde la UI.
  - Progreso en tiempo real (WebSocket).
- **Pausa/Reanuda/Cancela**:
  - Pausa global: detiene aria2 y mata el proceso yt-dlp activo.
  - Cancel por `id`: remueve en aria2 (si aplica) y borra parciales.
  - Aria2: remueve y borra parciales + sidecars (`.aria2`, `*.part`, `*.ytdl`).
  - yt-dlp: mata proceso activo y limpia temporales recientes.

---

## 🧱 Arquitectura (resumen)

- **Bot (python-telegram-bot + Telethon)**: intake de mensajes y comandos; ciclo de descargas asíncrono **en segundo plano** (no bloquea los comandos).
- **Descargas**:
  - `aria2` vía RPC (`addUri`, `addTorrent`, `pauseAll`, `remove`…).
  - `yt-dlp` como **subproceso cancelable** (o módulo en thread si no hay binario).
  - `Telethon` para media de Telegram con callback de progreso.
- **DB SQLite**: tablas `queue`, `progress`, `kv`, `events` (+ `ext_id` para GID de aria2).
- **Panel FastAPI**: REST + WebSocket con snapshot de estado cada 1s.
- **Control interno**: servidor local en `127.0.0.1:8765` para `/pause`, `/resume`, `/run`, `/cancel/{id}`.

---

## ✅ Requisitos

- **Windows 11**
- **Python 3.13**
- **aria2**:
  - `choco install aria2 -y`, o portable en `tools\aria2\aria2c.exe`.
- **Telegram**:
  - Crea un bot con [@BotFather](https://t.me/BotFather) → **BOT_TOKEN**.
  - Consigue **API_ID** y **API_HASH** en https://my.telegram.org → API Development Tools.

---

## 🚀 Instalación

```powershell
# 1) Entorno virtual
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2) Dependencias
pip install --upgrade pip
pip install -r requirements.txt
```

### Variables de entorno (`.env`)

Crea un archivo `.env` en la raíz:

```ini
# Telegram
BOT_TOKEN="123456:ABCDEF..."
API_ID=123456
API_HASH="xxxxxxxxxxxxxxxxxxxx"
TELETHON_STRING="pega_aqui_tu_string_session"  # generar con session_setup.py

# Sesión de usuario para Telethon
USE_TELETHON=true
TELETHON_SESSION_MODE="file"       # "file" o "string"
TELETHON_STRING=""                 # solo si usas mode=string
SESSIONS_DIR="./data/sessions"     # para sesiones file

# Descargas y panel
DOWNLOAD_DIR="./downloads"
TIMEZONE="America/New_York"

# Scheduler
SCHEDULE_HOUR=3   # hora base (24h)
# el modo ventana Start/Stop se ajusta desde /schedule

# aria2 RPC
ARIA2_ENDPOINT="http://127.0.0.1:6800/jsonrpc"
ARIA2_SECRET="XXXXXXX"  # si usas rpc-secret en aria2-config, ponlo aquí

# Panel (opcional)
PANEL_HOST="127.0.0.1"
PANEL_PORT=8080
PANEL_TOKEN="cualquier_string_segura"

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
```

---

## ▶️ Ejecución

### 1) Inicia **aria2** (RPC)
```powershell
- powershell -ExecutionPolicy Bypass -File .\scripts\run-aria2.ps1
o
- & (Get-Command aria2c.exe).Source --enable-rpc=true --rpc-listen-all=false --rpc-listen-port=6800 --check-certificate=false --file-allocation=none --max-connection-per-server=16 --split=16 --continue=true --rpc-secret=D0wnl04d3r --dir="$((Resolve-Path .).Path)\downloads"
```
- Usa `scripts\aria2-config.txt` (incluye `enable-rpc=true`).
- Si defines `rpc-secret`, replica el valor en `.env` (`ARIA2_SECRET`).

### 2) Inicia el **bot** (incluye control local 8765)
```powershell
& ".\.venv\Scripts\python.exe" -m tgdl.cli bot
```

### 3) (Opcional) Inicia el **panel** FastAPI
```powershell
& ".\.venv\Scripts\python.exe" -m tgdl.cli panel
```
Abre `http://127.0.0.1:8080/` (el HTML pedirá `PANEL_TOKEN` para acciones).

---

## 💬 Comandos del bot

- `/start`, `/help`. `/manu` — ayuda general y opciones.
- `/status`, `/list` — estado y cola.
- `/now` — ejecuta ciclo en segundo plano.
- `/pause` — pausa global (aria2 + yt-dlp).
- `/resume` — reanuda y ejecuta ciclo.
- `/cancel {id}` — cancela trabajo específico.
- `/clear` — pausa y **borra toda la cola** y progresos (db incluida).
- `/purge` — pausa y **borra registro de errores y completados**.
- `/retry` — reintenta las descargas.
- `/shedule` — activa/desactiva ventana horaria y programacion (Start/Stop)
- `/when` - modifica hora de inicio de descargas.

---

## 🧲 Magnet y `.torrent`

- **Magnet**: envía el magnet al chat → se encola y lo toma aria2 (carpeta `downloads/torrents/`).
- **.torrent (URL)**: pega la URL; el bot descarga el `.torrent` y lo envía a aria2.
- **.torrent (archivo)**: reenvía el `.torrent` al bot → se pasa a aria2; el `.torrent` local se puede borrar tras enviarlo.

---

## 🗂️ Organización de carpetas
Organización automática por tipo/origen:

```
downloads/
  torrents/
  youtube/Canal_o_Lista/
  example.com/
  Telegram Canal X/
```

---

## 🧹 Cancelación y limpieza

- **aria2**: remove(gid) + borrado de parciales y sidecars (.aria2, *.part, *.ytdl) reportados por tellStatus.files[].path.
- **yt-dlp**: al terminar el subproceso por cancelación, se limpian **temporales** (`*.part`, `*.ytdl`) recientes dentro de `DOWNLOAD_DIR`. - limpia temporales recientes (12h por defecto).

---

## 🔗 Acortadores de enlaces

- Cuando el destino es soporteable por **yt-dlp**, suele resolver bien redirecciones.
- Para **HTTP genérico**, se hace un `HEAD` con `allow_redirects=true`.
- Si el acortador usa validaciones anti-bot (JS/Cloudflare) → abre el enlace en navegador y reenvía la URL final.

---

## 🧰 Troubleshooting

- `ModuleNotFoundError: pydantic_settings` → `pip install pydantic-settings`
- Panel `401 Unauthorized` en `/` → abre `http://127.0.0.1:8080/` (root sin auth) y usa `PANEL_TOKEN`.
- Cancel no borra parciales → verifica `ext_id` en DB.

---

## 📁 Estructura del proyecto

```
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
```

---

## 🔐 Seguridad (hogar)

- Mantén el panel en `127.0.0.1` y no abras puertos a Internet.
- No compartas `.env` ni la DB.
- Las cabeceras HTTP para MediaFire se pasan a aria2 como header: ["K: V"] (p. ej. Referer)

---

## 💾 Backup / Restore

- DB SQLite en `data/queue.db`.
- Copiar con bot apagado:
  ```powershell
  New-Item -ItemType Directory -Force -Path .\backups | Out-Null
  Copy-Item .\data\queue.db .\backups\queue_$(Get-Date -Format yyyyMMdd_HHmmss).db
  ```

---

## 🛠️ Desarrollo

- **Iniciar aria2**: `& (Get-Command aria2c.exe).Source --enable-rpc=true --rpc-listen-all=false --rpc-listen-port=6800 --check-certificate=false --file-allocation=none --max-connection-per-server=16 --split=16 --continue=true --rpc-secret=D0wnl04d3r --dir="$((Resolve-Path .).Path)\downloads"`
- **Iniciar bot**: `& ".\.venv\Scripts\python.exe" -m tgdl.cli bot`
- **Iniciar panel**: `& ".\.venv\Scripts\python.exe" -m tgdl.cli panel`
- **Ejecutar tests**: `& .\.venv\Scripts\pytest.exe -q`
- **Publicar a GitHub**:
  ```powershell
  powershell -ExecutionPolicy Bypass -File .\scripts\publish.ps1 -Message "feat: commit inicial"
  ```

---
