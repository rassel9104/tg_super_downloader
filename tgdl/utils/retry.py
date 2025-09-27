from __future__ import annotations

import asyncio
import random
from collections.abc import Callable


def retry(source_type: str, tries: int = 3, base_delay: float = 0.5, jitter: bool = True):
    """
    Decorador de reintentos con backoff exponencial.
    - source_type: etiqueta para métricas/logs.
    - tries: reintentos totales (incluye el primer intento).
    """

    def _wrap(fn: Callable):
        if asyncio.iscoroutinefunction(fn):

            async def _arun(*args, **kwargs):
                from tgdl.core.logging import logger

                delay = base_delay
                last_exc = None
                for i in range(1, max(1, tries) + 1):
                    try:
                        return await fn(*args, **kwargs)
                    except Exception as e:
                        last_exc = e
                        if i >= tries:
                            logger.error("retry/%s exhausted after %d tries: %r", source_type, i, e)
                            break
                        sleep = delay + (random.uniform(0, delay) if jitter else 0.0)
                        logger.warning(
                            "retry/%s attempt=%d err=%r sleep=%.2fs", source_type, i, e, sleep
                        )
                        await asyncio.sleep(sleep)
                        delay *= 2
                raise last_exc

            return _arun
        else:

            def _run(*args, **kwargs):
                from tgdl.core.logging import logger

                delay = base_delay
                last_exc = None
                for i in range(1, max(1, tries) + 1):
                    try:
                        return fn(*args, **kwargs)
                    except Exception as e:
                        last_exc = e
                        if i >= tries:
                            logger.error("retry/%s exhausted after %d tries: %r", source_type, i, e)
                            break
                        sleep = delay + (random.uniform(0, delay) if jitter else 0.0)
                        logger.warning(
                            "retry/%s attempt=%d err=%r sleep=%.2fs", source_type, i, e, sleep
                        )
                        import time

                        time.sleep(sleep)
                        delay *= 2
                raise last_exc

            return _run

    return _wrap
