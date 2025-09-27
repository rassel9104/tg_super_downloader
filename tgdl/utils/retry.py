# CRLF
from __future__ import annotations

import asyncio
import random
from functools import wraps
from typing import Callable, TypeVar

T = TypeVar("T")


def retry(source_type: str, tries: int = 3, base_delay: float = 0.8):
    """
    Decorador de reintentos con backoff exponencial + jitter.
    Uso:
      @retry("http", tries=4, base_delay=0.6)
      async def fetch(...): ...
    """

    def deco(fn: Callable[..., T]):
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            delay = base_delay
            last_exc = None
            for _ in range(max(1, int(tries))):
                try:
                    return await fn(*args, **kwargs)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    last_exc = e
                    await asyncio.sleep(delay * (0.7 + 0.6 * random.random()))
                    delay *= 2.0
            raise last_exc

        return wrapper

    return deco
