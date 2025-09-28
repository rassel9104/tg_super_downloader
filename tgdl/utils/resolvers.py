# tgdl/utils/resolvers.py  (CRLF)
from __future__ import annotations

import asyncio
import contextlib
import re
from urllib.parse import urlparse

import httpx

_ALLOWED_MEDIAFIRE_HOSTS = {"mediafire.com", "www.mediafire.com"}
_ALLOWED_SF_HOSTS = {
    "sourceforge.net",
    "www.sourceforge.net",
    "downloads.sourceforge.net",
    "prdownloads.sourceforge.net",
}


async def resolve_mediafire_direct(url: str) -> tuple[str | None, dict[str, str] | None]:
    """
    Resuelve un enlace de mediafire.com/file/... a su URL de descarga directa.

    Diseño:
    - Evitamos exponer un parámetro llamado `timeout` en funciones async (regla ASYNC109).
      En su lugar, acotamos la operación con `asyncio.timeout(30.0)`.
    - No ejecuta JS; busca <a id="downloadButton" href="..."> en el HTML.
    - Retorna (None, None) si no encuentra el enlace directo.
    - Devuelve (direct_url, headers_dict) para que aria2 pueda usar Referer/User-Agent si hace falta.
    """
    # SSRF guard: solo dominios de mediafire
    try:
        host = (urlparse(url).hostname or "").lower()
        if not any(host == h or host.endswith("." + h) for h in _ALLOWED_MEDIAFIRE_HOSTS):
            return None, None
    except Exception:
        return None, None

    async with asyncio.timeout(30.0):
        # Timeout interno de httpx por robustez de red (no afecta ASYNC109)
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as cli:
            r = await cli.get(url)
            r.raise_for_status()
            direct = extract_mediafire_direct_link(r.text)
            if not direct:
                return None, None
            # Algunos mirrors de MediaFire requieren Referer para validar
            headers = {
                "Referer": url,
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                ),
            }
            return direct, headers


def extract_mediafire_direct_link(html: str) -> str | None:
    """
    Extrae la URL directa desde el HTML de MediaFire.

    1) Patrón principal:
       <a id="downloadButton" href="https://download...">Descargar</a>
    2) Fallback: cualquier href que apunte a subdominio 'download.*'
    """
    # 1) patrón principal: <a id="downloadButton" href="https://download...">
    m = re.search(
        r'id=["\']downloadButton["\'][^>]*href=["\'](?P<h>https?://[^"\']+)["\']',
        html,
        re.IGNORECASE,
    )
    if m:
        return m.group("h")

    # 2) fallback simple
    m = re.search(r'href=["\'](https?://download[^"\']+)["\']', html, re.IGNORECASE)
    return m.group(1) if m else None


async def resolve_sourceforge_direct(url: str) -> tuple[str | None, dict[str, str] | None]:
    """
    Resuelve un enlace de SourceForge (típicamente termina en /download) a la URL final
    del mirror (p. ej. *.dl.sourceforge.net). No descarga el binario, solo sigue redirecciones.
    Retorna (direct_url, headers) o (None, None).
    """
    try:
        host = (urlparse(url).hostname or "").lower()
        if not any(host == h or host.endswith("." + h) for h in _ALLOWED_SF_HOSTS):
            return None, None
    except Exception:
        return None, None

    # Asegurar sufijo /download para que SF seleccione mirror
    path = urlparse(url).path or ""
    req_url = url if path.endswith("/download") else (url.rstrip("/") + "/download")

    headers = {
        "Referer": url,
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "Accept": "*/*",
    }
    try:
        async with asyncio.timeout(30.0):
            async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as cli:
                resp = await cli.get(req_url, headers=headers, stream=True)
                direct = str(resp.url) if resp is not None else None
                with contextlib.suppress(Exception):
                    await resp.aclose()
        if not direct:
            return None, None
        return direct, headers
    except Exception:
        return None, None
