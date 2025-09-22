from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from pathlib import Path

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Telegram
    BOT_TOKEN: str | None = None
    API_ID: int | None = None
    API_HASH: str | None = None
    TELETHON_STRING: str | None = None

    # Rutas
    DOWNLOAD_DIR: Path = Field(default=Path("./downloads"))
    DB_PATH: Path = Field(default=Path("./data/queue.db"))

    # Zona horaria
    TIMEZONE: str = "America/New_York"

    # Scheduler
    SCHEDULE_HOUR: int = 2

    # aria2
    ARIA2_ENDPOINT: str = "http://127.0.0.1:6800/jsonrpc"
    ARIA2_SECRET: str | None = None

    # Panel/API
    PANEL_HOST: str = "127.0.0.1"
    PANEL_PORT: int = 8080
    PANEL_TOKEN: str = "p4nelT0k3n"

    LOG_LEVEL: str = "INFO"

settings = Settings()
