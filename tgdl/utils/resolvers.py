# tgdl/utils/resolvers.py  (CRLF)
from __future__ import annotations

import asyncio
import re

import httpx


async def resolve_mediafire_direct(url: str) -> str | None:
    """
    Resuelve un enlace de mediafire.com/file/... a su URL de descarga directa.

    Diseño:
    - Evitamos exponer un parámetro llamado `timeout` en funciones async (regla ASYNC109).
      En su lugar, acotamos la operación con `asyncio.timeout(30.0)`.
    - No ejecuta JS; busca <a id="downloadButton" href="..."> en el HTML.
    - Retorna None si no encuentra el enlace directo.
    """
    async with asyncio.timeout(30.0):
        # Timeout interno de httpx por robustez de red (no afecta ASYNC109)
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as cli:
            r = await cli.get(url)
            r.raise_for_status()
            direct = extract_mediafire_direct_link(r.text)
            return direct


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
