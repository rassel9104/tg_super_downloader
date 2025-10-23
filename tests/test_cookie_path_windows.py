# tests/test_cookie_path_windows.py
from pathlib import Path

import tgdl.adapters.downloaders.ytdlp as mod


def test_cookies_args_uses_resolved_path(monkeypatch, tmp_path):
    # Forzar ruta de cookies a una carpeta temporal (simula existencia)
    fake = tmp_path / "data" / "cookies" / "youtube.txt"
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_text("# netscape-cookie-file", encoding="utf-8")

    # Apuntar settings al archivo temporal
    monkeypatch.setattr(mod.settings, "YTDLP_COOKIES_FILE", str(fake), raising=False)
    monkeypatch.setattr(mod.settings, "YTDLP_COOKIES_MODE", "file", raising=False)

    args = mod._cookies_args()
    # Debe contener '--cookies' seguido de la ruta que pasamos (sin warnings ni escapes inválidos)
    assert "--cookies" in args
    assert str(fake) in args
    assert Path(args[args.index("--cookies") + 1]).exists()
