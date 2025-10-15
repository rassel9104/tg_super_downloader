import asyncio
from pathlib import Path

import pytest

import tgdl.adapters.downloaders.ytdlp as mod


class _FakeStdout:
    def __init__(self, lines):
        self._lines = [(ln + "\n").encode("utf-8") for ln in lines]
        self._i = 0

    async def readline(self):
        if self._i >= len(self._lines):
            return b""
        v = self._lines[self._i]
        self._i += 1
        return v


class _FakeProc:
    def __init__(self, lines, rc=0):
        self.stdout = _FakeStdout(lines)
        self._rc = rc

    async def wait(self):
        return self._rc

    def kill(self):
        pass


def _scenario_runner(scenarios):
    it = iter(scenarios)

    async def _create(*args, **kwargs):
        lines, rc = next(it)
        return _FakeProc(lines, rc)

    return _create


@pytest.mark.asyncio
async def test_with_subs_kw_supported_and_403_fallback(monkeypatch, tmp_path: Path):
    """
    1) cookies → 403
    2) cookies con fmt=best + extractor web → OK
    Verifica que _run acepta with_subs sin TypeError.
    """
    scenarios = [
        (
            [
                "[youtube] Extracting URL: https://youtu.be/x",
                "ERROR: unable to download video data: HTTP Error 403: Forbidden",
            ],
            1,
        ),
        (["[download] Destination: ok.mp4", "[download] 100% of 1.00MiB in 00:01"], 0),
    ]
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _scenario_runner(scenarios))
    monkeypatch.setattr(mod.shutil, "which", lambda _: "yt-dlp")
    ok = await mod.download_proc(
        url="https://youtu.be/x", outdir=tmp_path, allow_playlist=False, progress_cb=None
    )
    assert ok is True


@pytest.mark.asyncio
async def test_format_unavailable_then_bestvideo_best(monkeypatch, tmp_path: Path):
    scenarios = [
        (["ERROR: Requested format is not available"], 1),
        (["[download] Destination: ok.mp4", "[download] 100%"], 0),
    ]
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _scenario_runner(scenarios))
    monkeypatch.setattr(mod.shutil, "which", lambda _: "yt-dlp")
    ok = await mod.download_proc(
        url="https://youtu.be/y",
        outdir=tmp_path,
        allow_playlist=False,
    )
    assert ok


@pytest.mark.asyncio
async def test_dpapi_switch_to_file(monkeypatch, tmp_path: Path):
    scenarios = [
        (["ERROR: Failed to decrypt with DPAPI"], 1),
        (["[download] 100%"], 0),
    ]
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _scenario_runner(scenarios))
    monkeypatch.setattr(mod.shutil, "which", lambda _: "yt-dlp")
    # Forzar modo browser y archivo cookies existente
    mod.settings.YTDLP_COOKIES_MODE = "browser"
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text("# netscape\n", encoding="utf-8")
    import os

    monkeypatch.setattr(os.path, "exists", lambda p: str(p) == str(cookie_file))
    mod.settings.YTDLP_COOKIES_FILE = str(cookie_file)
    ok = await mod.download_proc(
        url="https://youtu.be/z",
        outdir=tmp_path,
        allow_playlist=False,
    )
    assert ok
