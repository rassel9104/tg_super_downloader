from __future__ import annotations

import asyncio
import time

from tgdl.utils.retry import retry


def test_retry_sync_succeeds_after_failures(monkeypatch):
    calls = {"n": 0}

    @retry("unit-sync", tries=3, base_delay=0.01, jitter=False)
    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("boom")
        return 42

    t0 = time.time()
    assert flaky() == 42
    assert calls["n"] == 3
    assert time.time() - t0 >= 0.01 + 0.02  # dos esperas


def test_retry_async(monkeypatch):
    calls = {"n": 0}

    @retry("unit-async", tries=2, base_delay=0.01, jitter=False)
    async def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("nope")
        return "ok"

    out = asyncio.get_event_loop().run_until_complete(flaky())
    assert out == "ok"
    assert calls["n"] == 2
