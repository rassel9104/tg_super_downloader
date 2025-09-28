import json
import types

import tgdl.adapters.telegram.bot_app as bot


class DummyBot:
    async def send_message(self, *a, **k):
        class _M:
            message_id = 1

        return _M()

    async def edit_message_text(self, *a, **k):
        class _M:
            message_id = 1

        return _M()


async def _run(monkeypatch):
    # Tarea vencida con URL de SourceForge
    def _due(now):
        payload = {
            "url": "https://sourceforge.net/projects/lyco/files/EvolutionX.zip/download",
            "notify_chat_id": 1000,
        }
        return [(1, "url", json.dumps(payload))]

    # run_cycle(force_all=True) usa db_get_all_queued() (sin argumentos)
    def _all():
        return _due(None)

    monkeypatch.setattr(bot, "db_get_all_queued", _all)
    # y por compatibilidad, también inyectamos db_get_due(now)
    monkeypatch.setattr(bot, "db_get_due", _due)
    monkeypatch.setattr(bot, "db_get_due", _due)  # por si cambia el flag
    monkeypatch.setattr(bot, "db_update_status", lambda *a, **k: None)
    monkeypatch.setattr(bot, "db_clear_progress", lambda *a, **k: None)
    monkeypatch.setattr(bot, "db_set_ext_id", lambda *a, **k: None)
    monkeypatch.setattr(bot, "db_get_flag", lambda *a, **k: "0")  # not paused
    monkeypatch.setattr(bot, "aria2_enabled", lambda: True)

    # Resolver SF -> URL directa + headers
    async def _sf(u):
        return (
            "https://ayera.dl.sourceforge.net/project/lyco/EvolutionX.zip",
            {"Referer": u, "User-Agent": "UA", "Accept": "*/*"},
        )

    # ¡Importante! bot_app importó el símbolo por nombre; parchea ahí:
    monkeypatch.setattr(bot, "resolve_sourceforge_direct", _sf)

    called = {}

    def _aria2_add(url, outdir, headers=None):
        called["url"] = url
        called["headers"] = headers or {}
        return "gid-xyz"

    monkeypatch.setattr(bot, "aria2_add", _aria2_add)

    app = types.SimpleNamespace(bot=DummyBot())
    await bot.run_cycle(app, force_all=True, notify_chat_id=1000)

    assert called.get("url", "").startswith("https://ayera.dl.sourceforge.net/")
    assert "Referer" in called["headers"] and "User-Agent" in called["headers"]


def test_bot_sourceforge_flow(event_loop, monkeypatch):
    event_loop.run_until_complete(_run(monkeypatch))
