# TG Super Downloader (hogar)

Bot de uso doméstico para descargar archivos a tu PC enviando enlaces por Telegram. Soporta:
- HTTP/HTTPS y magnéticos **(magnet:)** vía **aria2** (JSON-RPC)
- **.torrent** (desde URL o reenviado como archivo) → se pasa a aria2
- YouTube y muchas plataformas de video vía **yt-dlp**
- Descarga de **media de Telegram** vía **Telethon**
- Panel local (FastAPI) para pausar, reanudar, ejecutar, cancelar ítems, limpiar cola y ver progreso

> **Nota:** pensado para uso personal en red local. No multiusuario. Sin cuotas.

---

## Requisitos

- Windows 11
- Python 3.13 instalado (`py` o `python` en PATH)
- [aria2](https://aria2.github.io/) (instala con `choco install aria2` o portable en `tools\aria2\aria2c.exe`)
- Token de **Bot** ([@BotFather](https://t.me/BotFather))
- Credenciales **API_ID** y **API_HASH** de Telegram (https://my.telegram.org)

---

## Instalación

```powershell
# 1) Crear entorno
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2) Dependencias
pip install --upgrade pip
pip install -r requirements.txt
