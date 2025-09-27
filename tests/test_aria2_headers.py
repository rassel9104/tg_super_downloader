from __future__ import annotations

import tgdl.adapters.downloaders.aria2 as a2


def test_add_uri_maps_headers(monkeypatch, tmp_path):
    captured = {}

    def fake_call(method, params):
        captured["method"] = method
        captured["params"] = params
        return "G123"

    # monkeypatch ARIA2._call, no hacemos red real
    monkeypatch.setattr(a2.ARIA2, "_call", fake_call, raising=True)

    gid = a2.add_uri(
        "http://example.com/file", tmp_path, headers={"Referer": "x", "User-Agent": "ua"}
    )
    assert gid == "G123"
    assert captured["method"] == "aria2.addUri"
    params = captured["params"]
    assert isinstance(params, list) and isinstance(params[0], list)
    opts = params[1]
    assert "dir" in opts and "header" in opts
    assert any(h.startswith("Referer: ") for h in opts["header"])
    assert any(h.startswith("User-Agent: ") for h in opts["header"])
