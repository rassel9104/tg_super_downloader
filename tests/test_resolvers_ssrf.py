from __future__ import annotations

import pytest

from tgdl.utils.resolvers import resolve_mediafire_direct


@pytest.mark.asyncio
async def test_resolve_mediafire_domain_whitelist_blocks_other_hosts():
    # host no permitido => None inmediato (sin request real)
    assert await resolve_mediafire_direct("https://evil.example.com/file/abc") == (None, None)


@pytest.mark.asyncio
async def test_resolve_mediafire_accepts_allowed_host(monkeypatch):
    # Simula respuesta de httpx para dominio permitido
    class DummyResp:
        status_code = 200
        text = '<a id="downloadButton" href="https://download.mediafire.com/file.bin">x</a>'

        def raise_for_status(self):
            pass

    class DummyClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *e):
            return False

        async def get(self, url):
            return DummyResp()

    import tgdl.utils.resolvers as mod

    mod.httpx.AsyncClient = DummyClient
    url = "https://www.mediafire.com/file/someid/name"
    direct, hdrs = await resolve_mediafire_direct(url)
    assert direct == "https://download.mediafire.com/file.bin"
    assert isinstance(hdrs, dict) and "Referer" in hdrs
