# tests/test_ytdlp_subs429.py
import asyncio

import pytest

import tgdl.adapters.downloaders.ytdlp as mod


class _FakeStdout:
    def __init__(self, lines):
        self._lines = [(ln + "\n").encode() for ln in lines]

    async def readline(self):
        if not self._lines:
            return b""
        return self._lines.pop(0)


class _FakeProc:
    def __init__(self, lines, rc):
        self.stdout = _FakeStdout(lines)
        self._rc = rc

    async def wait(self):
        return self._rc

    def kill(self):
        pass


def _runner(scenarios):
    it = iter(scenarios)

    async def _create(*args, **kwargs):
        lines, rc = next(it)
        return _FakeProc(lines, rc)

    return _create


@pytest.mark.asyncio
async def test_403_then_429_subs_then_ok_without_subs(monkeypatch, tmp_path):
    """
    1) cookies → 403
    2) sin cookies (con subs) → 429 en subtítulos
    3) sin cookies (sin subs) → OK
    """
    scenarios = [
        (["ERROR: unable to download video data: HTTP Error 403: Forbidden"], 1),
        (
            [
                "[info] Downloading subtitles: es",
                "ERROR: Unable to download video subtitles for 'es': HTTP Error 429: Too Many Requests",
            ],
            1,
        ),
        (["[download] 100%"], 0),
    ]
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _runner(scenarios))
    # Evitar confusiones de path
    monkeypatch.setattr(mod.shutil, "which", lambda _: "yt-dlp")
    # Asegura que subtítulos no sean obligatorios para el test
    mod.settings.YTDLP_SUBS_REQUIRED = False

    ok = await mod.download_proc(
        url="https://youtu.be/FtIOg3MFHAg",
        outdir=tmp_path,
        allow_playlist=False,
    )
    assert ok is True
