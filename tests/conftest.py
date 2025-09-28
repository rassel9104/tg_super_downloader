# CRLF
import asyncio

import pytest

# Asegura el Policy correcto en Windows (Py 3.8+ ya usa Proactor por defecto).
# No cambiamos el policy global para no sorprender a otros tests.


@pytest.fixture(scope="session")
def event_loop():
    """Event loop compartido para tests async sin pytest-asyncio."""
    loop = asyncio.new_event_loop()
    try:
        yield loop
    finally:
        loop.run_until_complete(asyncio.sleep(0))
        loop.close()
