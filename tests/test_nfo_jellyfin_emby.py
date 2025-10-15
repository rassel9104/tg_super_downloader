# tests/test_nfo_jellyfin_emby.py
import os
import xml.etree.ElementTree as ET
from pathlib import Path

import tgdl.adapters.downloaders.ytdlp as mod


def _mk_info(**kw):
    base = {
        "id": "abc123",
        "title": "Sample Title",
        "fulltitle": "Sample Title – Full",
        "description": "Desc...",
        "duration": 125,
        "upload_date": "20240901",
        "uploader": "Canal X",
        "channel": "Canal X",
        "thumbnails": [
            {"url": "http://t/img1.jpg", "height": 360},
            {"url": "http://t/img2.jpg", "height": 720},
        ],
    }
    base.update(kw)
    return base


def test_movie_nfo_fields(tmp_path: Path, monkeypatch):
    os.environ.pop("YTDLP_NFO_TV_MODE", None)
    info = _mk_info()
    j = tmp_path / "Video [abc123].info.json"
    j.write_text(__import__("json").dumps(info), encoding="utf-8")
    created = mod._emit_nfo_for_recent(tmp_path)
    assert created == 1
    nfo = mod._nfo_path_for_json(j)
    assert nfo.exists()
    xml = ET.fromstring(nfo.read_text(encoding="utf-8"))
    assert xml.tag == "movie"
    assert xml.findtext("title") == "Sample Title"
    assert xml.findtext("studio") == "Canal X"
    uid = xml.find("uniqueid")
    assert uid is not None and uid.get("type") == "youtube" and uid.text == "abc123"
    assert xml.findtext("genre") == "YouTube"


def test_tvshow_and_episodes(tmp_path: Path, monkeypatch):
    os.environ["YTDLP_NFO_TV_MODE"] = "auto"
    # Simula playlist con 2 videos
    for idx in (1, 2):
        info = _mk_info(playlist_title="Mi Playlist", playlist_id="PL123", playlist_index=idx)
        j = tmp_path / "Mi Playlist" / f"Item{idx} [id{idx}].info.json"
        j.parent.mkdir(parents=True, exist_ok=True)
        j.write_text(__import__("json").dumps(info), encoding="utf-8")
    created = mod._emit_nfo_for_recent(tmp_path)
    # 2 episodios + 1 tvshow.nfo
    assert created == 2
    tv = tmp_path / "Mi Playlist" / "tvshow.nfo"
    assert tv.exists()
    tvxml = ET.fromstring(tv.read_text(encoding="utf-8"))
    assert tvxml.tag == "tvshow"
    assert tvxml.findtext("title") == "Mi Playlist"
    ep1 = tmp_path / "Mi Playlist" / "Item1 [id1].nfo"
    assert ep1.exists()
    epxml = ET.fromstring(ep1.read_text(encoding="utf-8"))
    assert epxml.tag == "episodedetails"
    assert epxml.findtext("season") == "1"
    assert epxml.findtext("episode") == "1"
