from __future__ import annotations
import json
import base64
from pathlib import Path
from typing import Any, Dict, Optional
import requests

from tgdl.config.settings import settings

_JSONRPC = "2.0"
_TIMEOUT = 15

def _rpc(method: str, params: list[Any] | None = None) -> Any:
    url = settings.ARIA2_ENDPOINT
    headers = {"Content-Type": "application/json"}
    p = []
    if settings.ARIA2_SECRET:
        p.append(f"token:{settings.ARIA2_SECRET}")
    if params:
        p.extend(params)
    body = {"jsonrpc": _JSONRPC, "method": method, "id": "tgdl", "params": p}
    r = requests.post(url, headers=headers, data=json.dumps(body), timeout=_TIMEOUT)
    r.raise_for_status()
    j = r.json()
    if "error" in j:
        raise RuntimeError(j["error"])
    return j.get("result")

def aria2_enabled() -> bool:
    try:
        _rpc("aria2.getVersion")
        return True
    except Exception:
        return False

def add_uri(url: str, outdir: Path, outname: str | None = None) -> str:
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    opts: Dict[str, Any] = {
        "dir": str(outdir),
        "continue": "true",
        "max-connection-per-server": "16",
        "split": "16",
        "timeout": "60",
        "check-certificate": "false",
        "auto-file-renaming": "false",
    }
    if outname:
        opts["out"] = outname
    # params = [[URLS], options]
    return _rpc("aria2.addUri", [[url], opts])

def tell_status(gid: str) -> dict[str, Any]:
    return _rpc("aria2.tellStatus", [gid, ["status", "totalLength", "completedLength", "downloadSpeed", "errorMessage", "files"]])

def pause(gid: str) -> Any:
    return _rpc("aria2.pause", [gid])

def unpause(gid: str) -> Any:
    return _rpc("aria2.unpause", [gid])

def remove(gid: str) -> Any:
    return _rpc("aria2.remove", [gid])

def pause_all() -> Any:
    return _rpc("aria2.pauseAll")

def unpause_all() -> Any:
    return _rpc("aria2.unpauseAll")

def get_global_stat() -> dict[str, Any]:
    return _rpc("aria2.getGlobalStat")

def add_torrent(torrent_path: Path, outdir: Path, outname: str | None = None) -> str:
    """
    Envía un .torrent a aria2 como binario base64 (RPC aria2.addTorrent).
    Devuelve GID.
    """
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    data = Path(torrent_path).read_bytes()
    torrent_b64 = base64.b64encode(data).decode("ascii")
    opts: Dict[str, Any] = {
        "dir": str(outdir),
        "continue": "true",
        "auto-file-renaming": "false",
        "check-certificate": "false",
    }
    if outname:
        opts["out"] = outname
    # params = [torrent(base64), uris (opcional), options]
    return _rpc("aria2.addTorrent", [torrent_b64, [], opts])