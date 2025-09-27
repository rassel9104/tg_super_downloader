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
    SCHEDULE_HOUR: int = 3

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


settings = Settings()
