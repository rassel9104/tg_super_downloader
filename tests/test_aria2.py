import json
from tgdl.adapters.downloaders import aria2  # :contentReference[oaicite:5]{index=5}

class _R:
    def __init__(self, payload): self._p=payload
    def raise_for_status(self): pass
    def json(self): return self._p

def test_add_uri_and_pause_all(monkeypatch, tmp_path):
    calls = []
    def fake_post(url, headers, data, timeout):
        calls.append(json.loads(data))
        method = calls[-1]["method"]
        if method == "aria2.addUri":
            return _R({"result":"GID-AAA"})
        elif method == "aria2.tellStatus":
            # ← tellStatus DEBE devolver un dict como en el API real
            return _R({"result":{
                "status": "active",
                "totalLength": "100",
                "completedLength": "10",
                "downloadSpeed": "0",
                "files": []
            }})
        elif method in ("aria2.pauseAll","aria2.getVersion","aria2.remove","aria2.unpauseAll","aria2.unpause"):
            # respuestas mínimas válidas
            return _R({"result":"OK"})
        return _R({"result":{}})

    monkeypatch.setattr("requests.post", fake_post)

    # ping
    assert aria2.aria2_enabled() is True
    gid = aria2.add_uri("https://ash-speed.hetzner.com/100MB.bin", tmp_path)
    assert gid == "GID-AAA"
    aria2.pause_all()
    aria2.unpause_all()
    aria2.remove("GID-AAA")
    st = aria2.tell_status("GID-AAA")
    assert isinstance(st, dict)  # ahora pasa

