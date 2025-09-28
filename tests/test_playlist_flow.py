# CRLF
import types

import tgdl.adapters.telegram.bot_app as bot


class DummyMsg:
    def __init__(self):
        self.chat_id = 42
        self.text = "https://www.youtube.com/playlist?list=PL123"
        self.message_id = 7

    async def reply_text(self, text, **kw):
        # Debe listar ítems y llevar teclado con pl:* acciones
        assert "📚" in text
        assert "Encolar" in kw["reply_markup"].inline_keyboard[1][0].text


class DummyUpdate:
    def __init__(self):
        self.message = DummyMsg()


class DummyCtx:
    def __init__(self):
        self.application = types.SimpleNamespace()


async def _probe(url, limit=10):
    return {
        "title": "Demo",
        "count": 3,
        "sample": [{"index": 1, "title": "A"}, {"index": 2, "title": "B"}],
    }


async def run_intake(monkeypatch):
    monkeypatch.setattr(bot.ytdlp, "probe_playlist", _probe)
    # No encolar: observar que no se llama a db_add para playlist en intake
    calls = []
    monkeypatch.setattr(bot, "db_add", lambda *a, **k: calls.append(a))
    await bot.intake(DummyUpdate(), DummyCtx())
    assert calls == []  # nada encolado hasta elegir


def test_playlist_preview(event_loop, monkeypatch):
    event_loop.run_until_complete(run_intake(monkeypatch))
