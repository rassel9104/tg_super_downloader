from __future__ import annotations

import json
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOG_DIR = Path("./logs")
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_LOG_FILE = _LOG_DIR / "tgsd.log"


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "name": record.name,
            "msg": record.getMessage(),
            "time": self.formatTime(record, "%Y-%m-%d %H:%M:%S"),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("tgdl")
    if logger.handlers:
        return logger
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Consola (humano)
    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logger.level)
    sh.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))

    # Archivo (JSON, rotación)
    fh = RotatingFileHandler(_LOG_FILE, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    fh.setLevel(logger.level)
    fh.setFormatter(_JsonFormatter())

    logger.addHandler(sh)
    logger.addHandler(fh)
    logger.propagate = False
    logger.info("logging initialized (file=%s)", str(_LOG_FILE))
    return logger


logger = setup_logging()
