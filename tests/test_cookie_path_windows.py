# tests/test_cookie_path_windows.py
from pathlib import Path

import tgdl.adapters.downloaders.ytdlp as mod


def test_resolve_cookie_file_default(monkeypatch, tmp_path):
    # Simula que no hay ruta definida en settings
    if hasattr(mod.settings, "YTDLP_COOKIES_FILE"):
        monkeypatch.setattr(mod.settings, "YTDLP_COOKIES_FILE", "", raising=False)
    p = mod._resolve_cookie_file()
    # Debe apuntar a data/cookies/youtube.txt (Path-safe en Windows)
    assert isinstance(p, Path)
    assert p.as_posix().endswith("data/cookies/youtube.txt")
