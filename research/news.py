"""Market news retrieval for the day / week / month summary windows.

Thin layer over research/web_searcher.py — the query execution, caching, DDGS →
HTTP fallback, sentiment scoring and ad filtering all already exist there and are
imported rather than duplicated. (analysis/macro.py duplicated that chain once
already; this module deliberately does not make it three.)

Public API:
    fetch_market_news(window, use_cache=True) -> dict

Output:
    {
      "window":          "1d" | "1w" | "1m",
      "headlines":       [{"snippet", "url", "domain", "sentiment_label"}],
      "sources":         ["reuters.com", ...],   # unique domains, ordered by first use
      "sentiment_score": float,                  # -1..+1 mean across headlines
      "sentiment_label": "Positive" | "Neutral" | "Negative",
    }
"""

import logging
from datetime import date
from urllib.parse import urlparse

from research.web_searcher import (
    _SITES_ANALYST,
    _SITES_CANADIAN,
    _SITES_NEWS,
    _SITES_RETAIL,
    _is_ad_url,
    _run_query,
    _truncate_words,
    extract_sentiment,
)

logger = logging.getLogger(__name__)

# All four site clusters, per the configured source selection: wire services,
# Canadian press, US market press, and analyst/research commentary.
_SITES_US_PRESS = "site:cnbc.com OR site:marketwatch.com OR site:barrons.com"

_MAX_HEADLINES   = 8
_SNIPPET_WORDS   = 34
_SENTIMENT_BAND  = 0.15   # same threshold web_searcher uses for the Neutral band


def _period_stamp(window: str) -> str:
    """A stamp that changes exactly as fast as the window it labels.

    This is load-bearing, not cosmetic. cache.py keys on the sha256 of the query
    string with a 24-hour TTL, so a static "stock market today" query would keep
    serving yesterday's headlines all day. Interpolating a period stamp rotates
    the cache key naturally — no TTL surgery needed.
    """
    today = date.today()
    if window == "1d":
        return today.strftime("%B %d %Y")
    if window == "1w":
        iso_year, iso_week, _ = today.isocalendar()
        return f"week {iso_week} {iso_year}"
    return today.strftime("%B %Y")


def _build_queries(window: str) -> list[str]:
    """Three queries per window, one per source emphasis."""
    stamp = _period_stamp(window)
    horizon = {
        "1d": "stock market today",
        "1w": "stock market this week recap",
        "1m": "stock market this month recap",
    }.get(window, "stock market")

    return [
        f"{horizon} {stamp} S&P 500 Nasdaq {_SITES_NEWS} OR {_SITES_US_PRESS}",
        f"TSX Canadian stock market {horizon} {stamp} {_SITES_CANADIAN}",
        f"market outlook analysis {stamp} {_SITES_ANALYST} OR {_SITES_RETAIL}",
    ]


def fetch_market_news(window: str, use_cache: bool = True) -> dict:
    """Fetch and score market headlines for one window. Never raises."""
    empty = {
        "window":          window,
        "headlines":       [],
        "sources":         [],
        "sentiment_score": 0.0,
        "sentiment_label": "Neutral",
    }

    try:
        results = []
        for query in _build_queries(window):
            try:
                results.extend(_run_query(query, use_cache=use_cache))
            except Exception as exc:
                logger.debug("Market news query failed (%s): %s", query[:60], exc)

        if not results:
            logger.warning("No market news returned for window %s.", window)
            return empty

        # Dedupe by URL, drop ad/redirect links and bodyless rows.
        seen: dict[str, str] = {}
        for r in results:
            href = (r.get("href") or "").strip()
            body = (r.get("body") or "").strip()
            if body and href and href not in seen and not _is_ad_url(href):
                seen[href] = body

        # Rank by how recent the snippet looks before truncating. DuckDuckGo
        # happily returns months-old articles for a "this week" query, and the
        # first result is the one quoted in the summary — so recency has to be an
        # ordering signal, not left to luck.
        ranked = sorted(seen.items(), key=lambda kv: -_recency_score(kv[1]))

        headlines = []
        scores    = []
        for href, body in ranked[:_MAX_HEADLINES]:
            score, label = extract_sentiment(body)
            scores.append(score)
            headlines.append({
                "snippet":         _truncate_words(body, _SNIPPET_WORDS),
                "url":             href,
                "domain":          _domain(href),
                "sentiment_label": label,
            })

        if not headlines:
            return empty

        mean = sum(scores) / len(scores)
        if mean > _SENTIMENT_BAND:
            label = "Positive"
        elif mean < -_SENTIMENT_BAND:
            label = "Negative"
        else:
            label = "Neutral"

        # Unique domains, first-use order.
        sources = list(dict.fromkeys(h["domain"] for h in headlines if h["domain"]))

        return {
            "window":          window,
            "headlines":       headlines,
            "sources":         sources,
            "sentiment_score": round(mean, 4),
            "sentiment_label": label,
        }
    except Exception as exc:
        logger.warning("fetch_market_news(%s) failed: %s", window, exc)
        return empty


_RECENCY_MARKERS = [
    (r"\b\d+\s*(minute|min|hour|hr)s?\s+ago\b", 100),
    (r"\b1\s*day\s+ago\b",                       90),
    (r"\b[1-7]\s*days?\s+ago\b",                 80),
    (r"\byesterday\b",                           85),
    (r"\btoday\b",                               85),
    (r"\b[1-4]\s*weeks?\s+ago\b",                40),
    (r"\b\d+\s*months?\s+ago\b",                 10),
    (r"\b\d+\s*years?\s+ago\b",                   0),
]


def _recency_score(body: str) -> int:
    """Heuristic freshness score from the date hint DuckDuckGo puts in a snippet.

    Snippets carry either a relative marker ("2 hours ago") or an absolute date
    ("Apr 28, 2026"). Neither is guaranteed, so an unmarked snippet scores in the
    middle rather than being pushed to the bottom — absence of a date is not
    evidence of staleness.
    """
    import re

    text = (body or "").lower()
    for pattern, score in _RECENCY_MARKERS:
        if re.search(pattern, text):
            return score

    # Absolute date: reward the current month, penalise older ones.
    today = date.today()
    if today.strftime("%b").lower() in text or today.strftime("%B").lower() in text:
        if str(today.year) in text:
            return 70
    for m in range(1, 13):
        month_abbr = date(today.year, m, 1).strftime("%b").lower()
        if month_abbr in text and m != today.month:
            return 20

    return 50   # no date hint at all — neutral, not penalised


def _domain(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""
