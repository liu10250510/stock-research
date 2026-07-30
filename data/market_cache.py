"""File-based cache for fetched market data, with a short TTL.

Mirrors cache.py (used for web-search results) but stores pickled payloads rather
than JSON, because a ticker's data dict holds pandas DataFrames.

The TTL is deliberately short: Yahoo Finance quotes are already delayed ~15 minutes,
so caching for that long costs no meaningful freshness while making repeat runs of
the same universe near-instant.

Usage:
    from data import market_cache

    hit = market_cache.get("AAPL")
    if hit is None:
        hit = expensive_fetch("AAPL")
        market_cache.put("AAPL", hit)
"""

import hashlib
import logging
import os
import pickle
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(__file__).parent.parent / ".market_cache"
TTL = 900  # seconds (15 min) — override in tests or via monkey-patch

# Bumped whenever the shape of a cached ticker dict changes, so stale entries
# written by an older version are never deserialised into new code.
SCHEMA_VERSION = 3


def _path(symbol: str) -> Path:
    key = f"v{SCHEMA_VERSION}:{symbol}"
    h = hashlib.sha256(key.encode()).hexdigest()[:20]
    return _CACHE_DIR / f"{h}.pkl"


def get(symbol: str):
    """Return the cached data dict for *symbol*, or None if missing or expired."""
    p = _path(symbol)
    try:
        if not p.exists():
            return None
        with p.open("rb") as fh:
            data = pickle.load(fh)
        if time.time() - data["ts"] > TTL:
            p.unlink(missing_ok=True)  # evict eagerly so the dir stays bounded
            return None
        return data["payload"]
    except Exception as exc:
        logger.debug("Market cache read failed for %s: %s", symbol, exc)
        return None


def put(symbol: str, payload) -> None:
    """Persist *payload* for *symbol*."""
    try:
        _CACHE_DIR.mkdir(exist_ok=True)
        tmp = _path(symbol).with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        with tmp.open("wb") as fh:
            pickle.dump({"ts": time.time(), "payload": payload}, fh,
                        protocol=pickle.HIGHEST_PROTOCOL)
        # Atomic rename — concurrent fetch threads never observe a partial file.
        tmp.replace(_path(symbol))
    except Exception as exc:
        logger.debug("Market cache write failed for %s: %s", symbol, exc)


def clear() -> int:
    """Delete all cached files. Returns count removed."""
    removed = 0
    try:
        for f in _CACHE_DIR.glob("*.pkl"):
            f.unlink(missing_ok=True)
            removed += 1
    except Exception:
        pass
    return removed
