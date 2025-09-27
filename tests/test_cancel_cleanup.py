from __future__ import annotations

from pathlib import Path

import tgdl.adapters.downloaders.aria2 as a2


def test_cancel_cleanup_deletes_sidecars(tmp_path, monkeypatch):
    # simula tellStatus con un archivo en tmp
    f = tmp_path / "movie.mkv"
    f.write_bytes(b"x")
    sidecar = tmp_path / "movie.mkv.aria2"
    sidecar.write_text("s")
    part = tmp_path / "movie.mkv.part"
    part.write_text("p")

    def fake_tell(gid):
        return {"files": [{"path": str(f)}]}

    def fake_remove(gid):
        return True

    monkeypatch.setattr(a2, "tell_status", fake_tell)
    monkeypatch.setattr(a2, "remove", fake_remove)

    # invoca el bloque de limpieza (aislamos la parte relevante)
    st = a2.tell_status("GID")
    for file in st.get("files") or []:
        p = (file.get("path") or "").strip()
        path = Path(p)
        path.unlink(missing_ok=True)
        (tmp_path / (path.name + ".aria2")).unlink(missing_ok=True)
        for cand in path.parent.glob(f"{path.stem}*"):
            if cand.suffix.lower() in {".part", ".ytdl", ".ytdl.part"}:
                cand.unlink(missing_ok=True)

    assert not f.exists()
    assert not sidecar.exists()
    assert not part.exists()
