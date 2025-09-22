import os
import types
import pytest
from pathlib import Path

@pytest.fixture
def temp_db_path(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir()
    return data / "queue.db"

@pytest.fixture(autouse=True)
def _isolate_settings(monkeypatch, temp_db_path):
    # Aísla la ruta de DB y el directorio de descargas para cada test
    from tgdl.config.settings import settings
    monkeypatch.setattr(settings, "DB_PATH", temp_db_path, raising=True)
    dld = temp_db_path.parent.parent / "downloads"
    dld.mkdir(exist_ok=True, parents=True)
    monkeypatch.setattr(settings, "DOWNLOAD_DIR", dld, raising=True)
    # Evita llamadas reales a aria2 por defecto
    monkeypatch.setenv("ARIA2_ENDPOINT", "http://127.0.0.1:6800/jsonrpc")
    return settings
