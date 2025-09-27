from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from tgdl.config.settings import settings


# ---------- Helpers de conexión ----------
def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    db_file = db_path or settings.DB_PATH
    db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_file, isolation_level=None, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


# ---------- Esquema ----------
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS queue (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  kind         TEXT NOT NULL,
  payload      TEXT NOT NULL,               -- JSON
  status       TEXT NOT NULL DEFAULT 'queued',
  scheduled_at TEXT NOT NULL,               -- ISO datetime
  created_at   TEXT NOT NULL,               -- ISO datetime
  updated_at   TEXT NOT NULL                -- ISO datetime
);

CREATE INDEX IF NOT EXISTS idx_queue_status      ON queue(status);
CREATE INDEX IF NOT EXISTS idx_queue_scheduled   ON queue(scheduled_at);
CREATE INDEX IF NOT EXISTS idx_queue_created     ON queue(created_at);

CREATE TABLE IF NOT EXISTS progress (
  qid         INTEGER PRIMARY KEY,
  total       INTEGER,                       -- bytes (NULL si desconocido)
  downloaded  INTEGER NOT NULL DEFAULT 0,    -- bytes
  updated_at  TEXT NOT NULL,                 -- ISO
  FOREIGN KEY(qid) REFERENCES queue(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS kv (
  k TEXT PRIMARY KEY,
  v TEXT
);

CREATE TABLE IF NOT EXISTS events (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  qid        INTEGER,
  ts         TEXT NOT NULL,
  type       TEXT NOT NULL,
  payload    TEXT NOT NULL,                  -- JSON
  FOREIGN KEY(qid) REFERENCES queue(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS schedules (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  rule        TEXT NOT NULL,                 -- JSON: descripción APScheduler (cron/interval/once)
  enabled     INTEGER NOT NULL DEFAULT 1,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);
"""


def db_init(db_path: Path | None = None) -> None:
    with _connect(db_path) as conn:
        conn.executescript(SCHEMA_SQL)


# ---------- Flags ----------
def db_set_flag(key: str, value: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (key, value),
        )


def db_get_flag(key: str, default: str | None = None) -> str | None:
    with _connect() as conn:
        cur = conn.execute("SELECT v FROM kv WHERE k=?", (key,))
        row = cur.fetchone()
        return row[0] if row else default


def is_paused() -> bool:
    return db_get_flag("PAUSED", "0") == "1"


# ---------- Queue ----------
def _iso_now() -> str:
    return datetime.now().astimezone().isoformat()


def db_add(kind: str, payload: dict[str, Any], scheduled_at: datetime) -> int:
    now_iso = _iso_now()
    sched_iso = scheduled_at.astimezone().isoformat()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO queue(kind, payload, status, scheduled_at, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (kind, json.dumps(payload, ensure_ascii=False), "queued", sched_iso, now_iso, now_iso),
        )
        return int(cur.lastrowid)


def db_get_due(now: datetime) -> list[tuple[int, str, str]]:
    """Elementos en 'queued' programados hasta 'now' (incl)."""
    now_iso = now.astimezone().isoformat()
    with _connect() as conn:
        cur = conn.execute(
            "SELECT id, kind, payload FROM queue "
            "WHERE status='queued' AND scheduled_at<=? "
            "ORDER BY id ASC",
            (now_iso,),
        )
        return list(cur.fetchall())


def db_get_all_queued() -> list[tuple[int, str, str]]:
    with _connect() as conn:
        cur = conn.execute(
            "SELECT id, kind, payload FROM queue WHERE status='queued' ORDER BY id ASC"
        )
        return list(cur.fetchall())


def db_update_status(qid: int, status: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE queue SET status=?, updated_at=? WHERE id=?",
            (status, _iso_now(), qid),
        )


def db_list(limit: int = 50) -> list[tuple[int, str, str, str, str]]:
    with _connect() as conn:
        cur = conn.execute(
            "SELECT id, kind, payload, status, scheduled_at FROM queue ORDER BY id DESC LIMIT ?",
            (int(limit),),
        )
        return list(cur.fetchall())


def db_purge_finished() -> int:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM queue WHERE status IN ('done','error')")
        return cur.rowcount


def db_retry_errors() -> int:
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE queue SET status='queued', updated_at=? WHERE status='error'",
            (_iso_now(),),
        )
        return cur.rowcount


def db_requeue_paused() -> int:
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE queue SET status='queued', updated_at=? WHERE status='paused'",
            (_iso_now(),),
        )
        return cur.rowcount


def db_requeue_paused_reschedule_now() -> int:
    now_iso = _iso_now()
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE queue SET status='queued', scheduled_at=?, updated_at=? WHERE status='paused'",
            (now_iso, now_iso),
        )
        return cur.rowcount


# ---------- Progreso ----------
def db_update_progress(qid: int, total: int | None, downloaded: int) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO progress(qid,total,downloaded,updated_at) "
            "VALUES (?,?,?,?) "
            "ON CONFLICT(qid) DO UPDATE SET total=excluded.total, downloaded=excluded.downloaded, updated_at=excluded.updated_at",
            (qid, total if (total or 0) > 0 else None, downloaded, _iso_now()),
        )


def db_clear_progress(qid: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM progress WHERE qid=?", (qid,))


# ---------- Eventos (opcional, para auditoría y panel) ----------
def db_add_event(qid: int | None, etype: str, payload: dict[str, Any]) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO events(qid, ts, type, payload) VALUES (?,?,?,?)",
            (qid, _iso_now(), etype, json.dumps(payload, ensure_ascii=False)),
        )
        return int(cur.lastrowid)


# ---------- Migraciones y utilidades varias ----------
def db_migrate_add_ext_id() -> None:
    """Asegura que queue tenga columna ext_id (para GID de aria2 u otros ids externos)."""
    with _connect() as conn:
        cur = conn.execute("PRAGMA table_info(queue)")
        cols = [r[1] for r in cur.fetchall()]
        if "ext_id" not in cols:
            conn.execute("ALTER TABLE queue ADD COLUMN ext_id TEXT")


def db_set_ext_id(qid: int, ext_id: str | None) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE queue SET ext_id=?, updated_at=? WHERE id=?", (ext_id, _iso_now(), qid)
        )


def db_get_queue(limit: int = 200):
    with _connect() as conn:
        cur = conn.execute(
            "SELECT id, kind, payload, status, scheduled_at, ext_id FROM queue ORDER BY id DESC LIMIT ?",
            (int(limit),),
        )
        return list(cur.fetchall())


def db_get_progress_rows(limit: int = 100):
    with _connect() as conn:
        cur = conn.execute(
            "SELECT qid,total,downloaded,updated_at FROM progress ORDER BY updated_at DESC LIMIT ?",
            (int(limit),),
        )
        return [
            {"qid": r[0], "total": r[1], "downloaded": r[2], "updated_at": r[3]}
            for r in cur.fetchall()
        ]


def db_clear_all() -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM progress")
        conn.execute("DELETE FROM queue")
        # opcional: limpiar flags, descomentando si lo deseas
        # conn.execute("DELETE FROM kv WHERE k IN ('PAUSED')")
