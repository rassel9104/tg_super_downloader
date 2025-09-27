from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import requests

from tgdl.config.settings import settings
from tgdl.core.logging import logger
from tgdl.utils.retry import retry


class Aria2Client:
    """
    Cliente mínimo para aria2 JSON-RPC con helpers de alto nivel.
    Respeta ARIA2_ENDPOINT y ARIA2_SECRET de .env
    """

    def __init__(self, endpoint: str | None = None, secret: str | None = None, timeout: int = 10):
        self.endpoint = (endpoint or settings.ARIA2_ENDPOINT).rstrip("/")
        self.secret = secret or settings.ARIA2_SECRET
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})

    # ====== núcleo JSON-RPC ======
    @retry("aria2-rpc", tries=4, base_delay=0.4, jitter=True)
    def _call(self, method: str, params: list[Any] | None = None) -> Any:
        payload = {"jsonrpc": "2.0", "id": "tgdl", "method": method}
        p = list(params) if params else []
        if self.secret:
            p = [f"token:{self.secret}", *p]
        payload["params"] = p

        resp = self._session.post(self.endpoint, data=json.dumps(payload), timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        if data.get("error"):
            err = data["error"]
            logger.error(
                "aria2._call error method=%s code=%s message=%s",
                method,
                err.get("code"),
                err.get("message"),
            )
            raise RuntimeError(f"aria2 error: {err}")
        return data.get("result")

    # ====== envoltorios más usados ======
    def get_version(self) -> dict:
        return self._call("aria2.getVersion", [])

    def tell_active(self) -> Any:
        return self._call("aria2.tellActive", [])

    def tell_status(self, gid: str) -> dict:
        # Campos habituales para limpieza/estado
        keys = ["status", "totalLength", "completedLength", "files", "errorCode", "errorMessage"]
        return self._call("aria2.tellStatus", [gid, keys])

    def pause_all(self) -> Any:
        return self._call("aria2.pauseAll", [])

    def unpause_all(self) -> Any:
        return self._call("aria2.unpauseAll", [])

    def remove(self, gid: str) -> Any:
        try:
            return self._call("aria2.remove", [gid])
        finally:
            # Intenta limpiar también resultados en estado "removed/error"
            try:
                self._call("aria2.removeDownloadResult", [gid])
            except Exception:
                pass

    def add_uri(
        self, uris: list[str], *, outdir: Path | None = None, options: dict | None = None
    ) -> str:
        opts = dict(options or {})
        if outdir:
            Path(outdir).mkdir(parents=True, exist_ok=True)
            opts["dir"] = str(outdir)
        params = [uris]
        if opts:
            params.append(opts)
        gid = self._call("aria2.addUri", params)
        return gid

    def add_torrent(
        self, torrent_path: Path, *, outdir: Path | None = None, options: dict | None = None
    ) -> str:
        """
        Sube un .torrent a aria2 usando aria2.addTorrent:
        params = [torrentContent(base64), uris(list) opcional, options dict]
        """
        torrent_path = Path(torrent_path)
        if not torrent_path.exists():
            raise FileNotFoundError(f"No existe el torrent: {torrent_path}")

        with open(torrent_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")

        opts = dict(options or {})
        if outdir:
            Path(outdir).mkdir(parents=True, exist_ok=True)
            opts["dir"] = str(outdir)

        params = [b64, [], opts]  # sin URIs adicionales
        gid = self._call("aria2.addTorrent", params)
        return gid


# Singleton compartido
ARIA2 = Aria2Client()


# ====== API de módulo que espera bot_app.py ======


def aria2_enabled() -> bool:
    """Devuelve True si el endpoint responde a getVersion()."""
    try:
        ARIA2.get_version()
        return True
    except Exception:
        logger.warning("aria2 not reachable at %s", ARIA2.endpoint)
        return False


def add_uri(url: str, outdir: Path, headers: dict[str, str] | None = None) -> str:
    """Añade una URL (http/https/magnet) y devuelve el GID."""
    opts: dict[str, Any] = {}
    if headers:
        # aria2 espera lista de strings "K: V"
        hdr_list = [f"{k}: {v}" for k, v in headers.items() if k and v is not None]
        if hdr_list:
            opts["header"] = hdr_list
    return ARIA2.add_uri([url], outdir=outdir, options=opts or None)


def add_torrent(torrent_path: Path, outdir: Path) -> str:
    """Añade un archivo .torrent y devuelve el GID."""
    return ARIA2.add_torrent(torrent_path, outdir=outdir, options=None)


def pause_all() -> Any:
    return ARIA2.pause_all()


def unpause_all() -> Any:
    return ARIA2.unpause_all()


def remove(gid: str) -> Any:
    return ARIA2.remove(gid)


def tell_status(gid: str) -> dict:
    return ARIA2.tell_status(gid)
