# CRLF
import pytest

from tgdl.utils.resolvers import extract_mediafire_direct_link, resolve_mediafire_direct


def test_extract_mediafire_direct_link_primary():
    html = """
    <html><body>
      <a id="downloadButton" href="https://download1580.mediafire.com/abc/file.bin">Download</a>
    </body></html>
    """
    direct = extract_mediafire_direct_link(html)
    assert direct == "https://download1580.mediafire.com/abc/file.bin"


def test_extract_mediafire_direct_link_fallback():
    html = """
    <html><body>
      <a class="btn" href="https://download123.mediafire.com/xyz/other.bin">Mirror</a>
    </body></html>
    """
    direct = extract_mediafire_direct_link(html)
    assert direct == "https://download123.mediafire.com/xyz/other.bin"


@pytest.mark.asyncio
async def test_resolve_mediafire_direct_ok(monkeypatch):
    html = """
    <html><body>
      <a id="downloadButton" href="https://download.mediafire.com/file-direct.bin">Download</a>
    </body></html>
    """

    class DummyResp:
        def __init__(self, text: str):
            self.text = text

        def raise_for_status(self):
            return None

    class DummyClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url: str):
            return DummyResp(html)

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: DummyClient())

    direct = await resolve_mediafire_direct("https://www.mediafire.com/file/abc/file")
    assert direct == "https://download.mediafire.com/file-direct.bin"
