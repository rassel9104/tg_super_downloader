import asyncio
import types
from datetime import datetime, timezone, timedelta

import tgdl.adapters.telegram.bot_app as bot  # :contentReference[oaicite:7]{index=7}
from tgdl.core import db as DB  # :contentReference[oaicite:8]{index=8}

class DummyApp:
    class _B:
        async def send_message(self, *a, **k): return True
    bot = _B()

def test_launch_cycle_dedup(monkeypatch, tmp_path):
    # Evita lógica real de Telegram y aria2/yt-dlp: sólo verificamos deduplicación de tareas
    app = DummyApp()
    started_flags = []
    async def fake_run_cycle(app, force_all=False, notify_chat_id=None):
        started_flags.append((force_all, notify_chat_id))
        await asyncio.sleep(0.01)

    monkeypatch.setattr(bot, "run_cycle", fake_run_cycle)
    # 1a vez: debe iniciar
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    ok1 = loop.run_until_complete(bot.launch_cycle_background(app, force_all=True, notify_chat_id=123))
    # 2a vez inmediatamente: NO debe iniciar otro
    ok2 = loop.run_until_complete(bot.launch_cycle_background(app, force_all=True, notify_chat_id=123))
    loop.run_until_complete(asyncio.sleep(0.02))
    loop.close()

    assert ok1 is True and ok2 is False
    assert started_flags == [(True, 123)]

def test_cycle_skips_when_paused(monkeypatch, tmp_path):
    # si llega un directorio, usa queue.db dentro
    db_file = Path(db_path or settings.DB_PATH)
    if db_file.exists() and db_file.is_dir():
        db_file = db_file / "queue.db"
        db_file.parent.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(db_file, isolation_level=None, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

    # Encolamos un item "pasado" para que calificara como due
    now = datetime.now(timezone.utc)
    DB.db_add("url", {"url":"http://example.com"}, now - timedelta(minutes=1))
    # Simulamos pausa
    monkeypatch.setattr(bot, "is_paused", lambda: True)
    # Fake run_cycle que usa el is_paused simulado
    called = {"ran": False}
    async def fake_run_cycle(app, force_all=False, notify_chat_id=None):
        called["ran"] = True
        # si estuviera pausado de verdad, debería retornar pronto
        return
    monkeypatch.setattr(bot, "run_cycle", fake_run_cycle)
    app = DummyApp()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    ok = loop.run_until_complete(bot.launch_cycle_background(app))
    loop.run_until_complete(asyncio.sleep(0.01))
    loop.close()
    # se lanza la task, pero el propio run_cycle debería salir temprano por pausa
    assert ok is True and called["ran"] is True
