"""
Tiny TTL + LRU cache for idempotent tool calls.

Agents frequently re-issue the same lookup within a single reasoning trace (the
critic revision loop especially re-runs searches). Caching idempotent, network-
bound tools — like web search — cuts latency and external calls with no behaviour
change. Exceptions are never cached, so transient failures still retry.
"""
from __future__ import annotations

import functools
import time
from collections import OrderedDict
from loguru import logger


def ttl_cache(ttl_seconds: int = 300, maxsize: int = 128):
    """Decorator: cache a function's successful return value by its arguments.

    Args:
        ttl_seconds: entry lifetime; <= 0 disables caching entirely.
        maxsize: max distinct keys held (LRU eviction).
    """
    def decorator(fn):
        store: "OrderedDict[tuple, tuple[float, object]]" = OrderedDict()

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if ttl_seconds <= 0:
                return fn(*args, **kwargs)

            key = (args, tuple(sorted(kwargs.items())))
            now = time.monotonic()

            hit = store.get(key)
            if hit is not None and hit[0] > now:
                store.move_to_end(key)
                logger.debug(f"[cache] hit {fn.__name__}{key[0]}")
                return hit[1]

            value = fn(*args, **kwargs)   # exceptions propagate uncached
            store[key] = (now + ttl_seconds, value)
            store.move_to_end(key)
            while len(store) > maxsize:
                store.popitem(last=False)
            return value

        wrapper.cache_clear = store.clear  # type: ignore[attr-defined]
        return wrapper

    return decorator
