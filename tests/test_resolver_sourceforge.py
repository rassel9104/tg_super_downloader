import tgdl.utils.resolvers as r


class DummyResp:
    def __init__(self, url):
        self.url = url

    async def aclose(self):
        pass


class DummyClient:
    def __init__(self, url):
        self._url = url

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, req_url, headers=None, stream=False):
        return DummyResp(self._url)


async def _run(monkeypatch):
    # Simula redirección a un mirror real
    monkeypatch.setattr(
        r.httpx,
        "AsyncClient",
        lambda **kw: DummyClient("https://ayera.dl.sourceforge.net/project/lyco/EvolutionX.zip"),
    )
    direct, hdrs = await r.resolve_sourceforge_direct(
        "https://sourceforge.net/projects/lyco/files/EvolutionX.zip/download"
    )
    assert direct and direct.startswith("https://ayera.dl.sourceforge.net/")
    assert "Referer" in hdrs and "User-Agent" in hdrs and "Accept" in hdrs


def test_resolver_sourceforge(event_loop, monkeypatch):
    event_loop.run_until_complete(_run(monkeypatch))
