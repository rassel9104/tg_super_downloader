# TG Super Downloader (uso hogareño)

Bot doméstico para enviarle enlaces por Telegram y descargar archivos directamente a tu PC (Windows 11).  
Soporta **HTTP/HTTPS**, **magnet:**, **.torrent**, **YouTube/streaming (yt-dlp)** y **media de Telegram (Telethon)**.  
Incluye un **panel local** (FastAPI) para pausar/reanudar/ejecutar/cancelar/limpiar y ver progreso.

> ⚠️ Pensado para uso **personal** en red local. No multiusuario, no cuotas. Mantén tus credenciales y puertos privados.

---

## ✨ Características

- **Descargas desde Telegram**: reenvía un mensaje con media o un enlace y se encola automáticamente.
- **HTTP/HTTPS** con **aria2** (JSON-RPC).
- **Magnet y .torrent**:
  - Magnet → lo toma aria2.
  - `.torrent` (URL o archivo reenviado) → se pasa a aria2 con `aria2.addTorrent`.
- **YouTube/otros** con **yt-dlp** (módulo o binario).
- **Pausa/Reanuda/Cancela**:
  - Pausa global: detiene aria2 y mata el proceso yt-dlp activo.
  - Cancel por `id`: remueve en aria2 (si aplica) y borra parciales.
- **Organización de carpetas** por origen:
  ```
  downloads/
    torrents/
    youtube/
    example.com/
    Telegram Canal X/
  ```
- **Panel local** (opcional) en `http://127.0.0.1:8080/`.

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

# Descargas y panel
DOWNLOAD_DIR="./downloads"
TIMEZONE="America/Chicago"

# aria2 RPC
ARIA2_ENDPOINT="http://127.0.0.1:6800/jsonrpc"
ARIA2_SECRET=""  # si usas rpc-secret en aria2-config, ponlo aquí

# Panel (opcional)
PANEL_HOST="127.0.0.1"
PANEL_PORT=8080
PANEL_TOKEN="cualquier_string_segura"
```

Genera **TELETHON_STRING**:
```powershell
python .\session_setup.py
```

---

## ▶️ Ejecución

### 1) Inicia **aria2** (RPC)
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-aria2.ps1
```
- Usa `scripts\aria2-config.txt` (incluye `enable-rpc=true`).
- Si defines `rpc-secret`, replica el valor en `.env` (`ARIA2_SECRET`).

### 2) Inicia el **bot** (incluye control local 8765)
```powershell
.\.venv\Scripts\Activate.ps1
python -m tgdl.cli bot
```

### 3) (Opcional) Inicia el **panel** FastAPI
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-panel.ps1
```
Abre `http://127.0.0.1:8080/` (el HTML pedirá `PANEL_TOKEN` para acciones).

---

## 💬 Comandos del bot

- `/start` — ayuda general.
- `/status`, `/list` — estado y cola.
- `/now` — ejecuta ciclo en segundo plano.
- `/pause` — pausa global (aria2 + yt-dlp).
- `/resume` — reanuda y ejecuta ciclo.
- `/cancel {id}` — cancela trabajo específico.
- `/clear` — pausa y **borra toda la cola** y progresos.

---

## 🧲 Magnet y `.torrent`

- **Magnet**: envía el magnet al chat → se encola y lo toma aria2 (carpeta `downloads/torrents/`).
- **.torrent (URL)**: pega la URL; el bot descarga el `.torrent` y lo envía a aria2.
- **.torrent (archivo)**: reenvía el `.torrent` al bot → se pasa a aria2; el `.torrent` local se puede borrar tras enviarlo.

---

## 🗂️ Organización de carpetas

```
downloads/
  torrents/
  youtube/
  example.com/
  Telegram Canal X/
```

---

## 🧹 Cancelación y limpieza

- **aria2**: al cancelar, se llama a `remove(gid)` y se consultan los `files` con `tellStatus` para borrar parciales si se desea.
- **yt-dlp**: al terminar el subproceso por cancelación, se limpian **temporales** (`*.part`, `*.ytdl`) recientes dentro de `DOWNLOAD_DIR`.

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

---

## 💾 Backup / Restore

- DB SQLite en `data/queue.db`.  
- Copiar con bot apagado:
  ```powershell
  Copy-Item .\data\queue.db .\backups\queue_YYYYMMDD.db
  ```

---

## 🛠️ Desarrollo

- **Iniciar aria2**: `powershell -ExecutionPolicy Bypass -File .\scripts\run-aria2.ps1`
- **Iniciar bot**: `python -m tgdl.cli bot`
- **Iniciar panel**: `powershell -ExecutionPolicy Bypass -File .\scripts\run-panel.ps1`
- **Publicar a GitHub**:  
  ```powershell
  powershell -ExecutionPolicy Bypass -File .\scripts\publish.ps1 -Message "feat: commit inicial"
  ```

---

## 📜 Licencia

MIT (opcional).
