import asyncio
from pathlib import Path
from tgdl.adapters.downloaders import ytdlp  # :contentReference[oaicite:6]{index=6}

class _Proc:
    def __init__(self): self.returncode = None
    async def wait(self): await asyncio.sleep(0.01); self.returncode = 0; return 0
    def terminate(self): self.returncode = -15
    def kill(self): self.returncode = -9

async def _run_with_bin(monkeypatch, tmp_path: Path):
    # finge que hay binario yt-dlp
    monkeypatch.setattr("shutil.which", lambda x: "yt-dlp.exe")
    async def fake_exec(*args, **kw): return _Proc()
    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    ok = await ytdlp.download_proc("https://youtu.be/xyz", tmp_path)
    return ok

async def _run_without_bin(monkeypatch, tmp_path: Path):
    # sin binario → fallback a módulo (to_thread -> download)
    monkeypatch.setattr("shutil.which", lambda x: None)
    called = {"download":0}
    def fake_download(url, outdir): called["download"]+=1; return True
    monkeypatch.setattr(ytdlp, "download", fake_download)
    ok = await ytdlp.download_proc("https://youtu.be/xyz", tmp_path)
    return ok, called["download"]

def test_ytdlp_async_proc_with_binary(event_loop, monkeypatch, tmp_path):
    ok = event_loop.run_until_complete(_run_with_bin(monkeypatch, tmp_path))
    assert ok is True

def test_ytdlp_async_proc_without_binary(event_loop, monkeypatch, tmp_path):
    ok, calls = event_loop.run_until_complete(_run_without_bin(monkeypatch, tmp_path))
    assert ok is True and calls == 1
