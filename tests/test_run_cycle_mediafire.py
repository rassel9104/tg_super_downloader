# CRLF
import json
import types

import tgdl.adapters.telegram.bot_app as bot


class DummyBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, parse_mode=None):
        self.messages.append(("send", text))

        class _M:
            message_id = 1

        return _M()

    async def edit_message_text(self, chat_id, message_id, text, parse_mode=None):
        self.messages.append(("edit", text))

        class _M:
            message_id = message_id

        return _M()


async def run_once(monkeypatch):
    # DB stubs
    updates = []

    def _get_due(now):
        return [
            (1, "url", json.dumps({"url": "https://mediafire.com/file/xyz", "notify_chat_id": 111}))
        ]

    monkeypatch.setattr(bot, "db_get_due", _get_due)
    monkeypatch.setattr(bot, "db_update_status", lambda *a, **k: updates.append(("status", a)))
    monkeypatch.setattr(bot, "db_clear_progress", lambda *a, **k: updates.append(("clear", a)))
    monkeypatch.setattr(bot, "db_set_ext_id", lambda *a, **k: None)
    monkeypatch.setattr(bot, "db_get_flag", lambda *a, **k: "0")  # not paused

    # aria2 stubs
    monkeypatch.setattr(bot, "aria2_enabled", lambda: True)
    monkeypatch.setattr(bot, "aria2_add", lambda url, outdir, headers=None: "gid-1")

    states = [
        {
            "status": "active",
            "totalLength": "100",
            "completedLength": "50",
            "files": [{"path": "X"}],
        },
        {
            "status": "complete",
            "totalLength": "100",
            "completedLength": "100",
            "files": [{"path": "X"}],
        },
    ]

    def _tell(gid):
        return states.pop(0) if states else {"status": "complete", "files": [{"path": "X"}]}

    monkeypatch.setattr(bot, "aria2_tell", _tell)

    # mediafire resolver
    async def _res(u):
        return ("https://download.mediafire.com/file/xyz.bin", {"Referer": "..."})

    monkeypatch.setattr(bot, "resolve_mediafire_direct", _res)

    # notify bot
    app = types.SimpleNamespace(bot=DummyBot())
    await bot.run_cycle(app, force_all=True, notify_chat_id=111)

    # Asegurar que 'done' se marca DESPUÉS del 'complete'
    assert ("status", (1, "done")) in updates


def test_mediafire_waits_for_completion(event_loop, monkeypatch):
    event_loop.run_until_complete(run_once(monkeypatch))
