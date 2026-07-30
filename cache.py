"""File-based cache for search query results with a configurable TTL.

Stores each query's results in .search_cache/<sha256-prefix>.json.
Default TTL is 24 hours so repeated runs within a day produce stable output.
"""

import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(__file__).parent / ".search_cache"
TTL = 86_400  # seconds — override in tests or via monkey-patch

# Empty results are cached so failing queries aren't retried on every run, but for
# much less time: an empty list is often a transient throttle rather than a real
# "nothing found", and we don't want to pin that for a full day.
EMPTY_TTL = 1_800


def _path(query: str) -> Path:
    h = hashlib.sha256(query.encode()).hexdigest()[:20]
    return _CACHE_DIR / f"{h}.json"


def get(query: str) -> list | None:
    """Return cached results for *query*, or None if missing or expired."""
    p = _path(query)
    try:
        if not p.exists():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        results = data["results"]
        ttl = TTL if results else EMPTY_TTL
        if time.time() - data["ts"] > ttl:
            p.unlink(missing_ok=True)  # evict eagerly so the dir stays bounded
            return None
        return results
    except Exception as exc:
        logger.debug("Cache read failed for %r: %s", query[:60], exc)
        return None


def put(query: str, results: list) -> None:
    """Persist *results* for *query*."""
    try:
        _CACHE_DIR.mkdir(exist_ok=True)
        p = _path(query)
        tmp = p.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(
            json.dumps({"ts": time.time(), "results": results}, ensure_ascii=False),
            encoding="utf-8",
        )
        # Atomic rename — concurrent research threads never see a partial file.
        tmp.replace(p)
    except Exception as exc:
        logger.debug("Cache write failed for %r: %s", query[:60], exc)


def clear() -> int:
    """Delete all cached files. Returns count removed."""
    removed = 0
    try:
        for f in _CACHE_DIR.glob("*.json"):
            f.unlink(missing_ok=True)
            removed += 1
    except Exception:
        pass
    return removed
