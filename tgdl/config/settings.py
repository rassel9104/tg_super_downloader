import os
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Telegram
    BOT_TOKEN: str | None = None
    API_ID: int | None = None
    API_HASH: str | None = None
    TELETHON_STRING: str | None = None

    # NUEVO: control de Telethon
    USE_TELETHON: bool = True  # habilitar/deshabilitar Telethon
    TELETHON_SESSION_MODE: str = "string"  # "string" | "file"
    TELETHON_SESSION_BASE: str = "tgdl"  # prefijo del nombre de sesión (file mode)
    SESSIONS_DIR: Path = Field(default=Path("./data/sessions"))

    # Rutas
    DOWNLOAD_DIR: Path = Field(default=Path("./downloads"))
    DB_PATH: Path = Field(default=Path("./data/queue.db"))

    # Zona horaria
    TIMEZONE: str = "America/New_York"

    # Scheduler
    SCHEDULE_HOUR: int = 24 / 7  # 0-23 o 24/7 para siempre activo

    # aria2
    ARIA2_ENDPOINT: str = "http://127.0.0.1:6800/jsonrpc"
    ARIA2_SECRET: str | None = None

    # Panel/API
    PANEL_HOST: str = "127.0.0.1"
    PANEL_PORT: int = 8080
    PANEL_TOKEN: str = "p4nelT0k3n"

    # ==== yt-dlp (nuevo) ====
    YTDLP_COOKIES: str | None = None  # ruta a cookies.txt (opcional)
    YTDLP_FORCE_IPV4: bool = False  # True fuerza IPv4
    YTDLP_PROXY: str | None = None  # p.ej. http://user:pass@host:port
    YTDLP_FORMAT: str = "bv*+ba/b"  # puedes ajustarlo si quieres audio-only, etc.
    YTDLP_MERGE_FORMAT: str = "mp4"
    YTDLP_CONCURRENT_FRAGMENTS: int = 4
    YTDLP_HTTP_CHUNK_SIZE: int = 1048576  # 1 MiB
    YTDLP_THROTTLED_RATE: int = 1048576  # 1 MiB/s

    PLAYLIST_DEFAULT_ACTION: Literal["ask", "playlist", "single"] = "ask"
    YTDLP_MAX_RUN_SECS: int = 600

    LOG_LEVEL: str = "INFO"


# --- Control de arranque de ciclo al encolar (por defecto: NO) ---
AUTORUN_ON_INTAKE: bool = os.getenv("AUTORUN_ON_INTAKE", "0").strip() == "1"

# --- Throttle de progreso en Telegram ---
# Editar mensaje cuando el % sube al menos este paso (por defecto 15)
try:
    PROGRESS_MIN_PCT_STEP: int = max(1, int(os.getenv("PROGRESS_MIN_PCT_STEP", "20")))
except Exception:
    PROGRESS_MIN_PCT_STEP = 20

# Enviar keep-alive si no hubo salto de % en este tiempo (segundos; por defecto 120)
try:
    PROGRESS_KEEPALIVE_SEC: int = max(30, int(os.getenv("PROGRESS_KEEPALIVE_SEC", "180")))
except Exception:
    PROGRESS_KEEPALIVE_SEC = 180


# --- Notificador global de progreso (resumen) ---
# 0 = deshabilitado (recomendado); 1 = habilitado
PROGRESS_SUMMARY_ENABLE: bool = os.getenv("PROGRESS_SUMMARY_ENABLE", "0").strip() == "1"
# intervalo del loop del resumen (segundos)
try:
    PROGRESS_SUMMARY_EVERY: int = max(20, int(os.getenv("PROGRESS_SUMMARY_EVERY", "120")))
except Exception:
    PROGRESS_SUMMARY_EVERY = 90
# separación mínima por ítem entre apariciones en el resumen (segundos)
try:
    PROGRESS_SUMMARY_MIN_SEP: int = max(45, int(os.getenv("PROGRESS_SUMMARY_MIN_SEP", "150")))
except Exception:
    PROGRESS_SUMMARY_MIN_SEP = 210

settings = Settings()
