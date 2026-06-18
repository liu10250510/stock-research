"""File-based cache for search query results with a configurable TTL.

Stores each query's results in .search_cache/<sha256-prefix>.json.
Default TTL is 24 hours so repeated runs within a day produce stable output.
"""

import hashlib
import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(__file__).parent / ".search_cache"
TTL = 86_400  # seconds — override in tests or via monkey-patch


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
        if time.time() - data["ts"] > TTL:
            return None
        return data["results"]
    except Exception as exc:
        logger.debug("Cache read failed for %r: %s", query[:60], exc)
        return None


def put(query: str, results: list) -> None:
    """Persist *results* for *query*."""
    try:
        _CACHE_DIR.mkdir(exist_ok=True)
        _path(query).write_text(
            json.dumps({"ts": time.time(), "results": results}, ensure_ascii=False),
            encoding="utf-8",
        )
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
