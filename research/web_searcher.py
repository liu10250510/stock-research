"""
Web Research, Sentiment & Risk Factor Extraction (Step 6, §10).

Runs 3 targeted DuckDuckGo searches per ticker against curated financial
sites, then scores sentiment and extracts named risk categories.

Public API:
    search_ticker(symbol, name)        -> dict  (§10.5 output)
    extract_sentiment(text)            -> (float, str)
    extract_risk_factors(text)         -> list[dict]

Site clusters used:
    Analyst ratings  : marketbeat.com, zacks.com, seekingalpha.com
    Forecast/retail  : fool.com, simplywall.st
    Financial news   : reuters.com, finance.yahoo.com
    Canadian news    : theglobeandmail.com, financialpost.com, bnnbloomberg.ca
                       (added automatically for .TO tickers)

Usage:
    from research.web_searcher import search_ticker
    result = search_ticker("AAPL", "Apple")
    result = search_ticker("RY.TO", "Royal Bank of Canada")
"""

import logging
import re
import time

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

try:
    import cache as _cache
except ImportError:
    _cache = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Site clusters
# ---------------------------------------------------------------------------

# Politeness delay applied per real network query (never on a cache hit).
_QUERY_DELAY = 0.5

# One pooled session for the HTTP fallback — avoids a fresh TCP+TLS handshake
# per query. requests.Session is thread-safe for plain GETs.
_SESSION = requests.Session()
_SESSION.headers.update(
    {"User-Agent": "Mozilla/5.0 (compatible; StockResearch/1.0)"}
)

_SITES_ANALYST  = "site:marketbeat.com OR site:zacks.com OR site:seekingalpha.com"
_SITES_NEWS     = "site:reuters.com OR site:finance.yahoo.com"
_SITES_RETAIL   = "site:fool.com OR site:simplywall.st"
_SITES_CANADIAN = "site:theglobeandmail.com OR site:financialpost.com OR site:bnnbloomberg.ca"

# ---------------------------------------------------------------------------
# §10.3  Sentiment keywords
# ---------------------------------------------------------------------------

POSITIVE_WORDS = {
    "upgrade", "outperform", "buy", "strong buy", "overweight", "bullish",
    "beat", "beat expectations", "growth", "momentum", "positive", "gains",
    "raised", "increase", "record", "upside", "opportunity",
}

NEGATIVE_WORDS = {
    "downgrade", "underperform", "sell", "underweight", "bearish",
    "miss", "missed expectations", "weakness", "concern", "risk",
    "cut", "lowered", "decline", "loss", "warning", "headwind", "pressure",
}

# ---------------------------------------------------------------------------
# §10.4  Risk categories
# ---------------------------------------------------------------------------

RISK_CATEGORIES = {
    "Valuation risk":      ["overvalued", "expensive", "stretched valuation", "high p/e", "premium"],
    "Competition risk":    ["competition", "competitor", "market share", "rival"],
    "Regulatory risk":     ["regulation", "regulatory", "antitrust", "investigation", "lawsuit", "fine"],
    "Macro/rate risk":     ["interest rate", "inflation", "recession", "slowdown", "fed", "bank of canada"],
    "Debt/liquidity risk": ["debt", "leverage", "cash flow", "liquidity", "credit"],
    "Execution risk":      ["guidance cut", "missed guidance", "management change", "restructuring"],
    "Geopolitical risk":   ["tariff", "trade war", "china", "sanctions", "political"],
}

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def search_ticker(symbol: str, name: str) -> dict:
    """Run web research for one ticker and return sentiment + risk output.

    Args:
        symbol: Ticker symbol, e.g. "AAPL" or "RY.TO".
        name:   Display name, e.g. "Apple" or "Royal Bank of Canada".

    Returns:
        Dict matching §10.5 output structure. Never raises.
    """
    _empty = {
        "raw_summary":     None,
        "sentiment_score": 0.0,
        "sentiment_label": "Neutral",
        "risk_factors":    [],
        "sources":         [],
        "source_scores":   [],
    }

    try:
        queries = _build_queries(symbol, name)
        results = []
        for q in queries:
            # Politeness delay lives inside _run_query, after the cache check,
            # so warm-cache runs don't sleep for nothing.
            results.extend(_run_query(q))

        if not results:
            logger.warning("%s: no search results returned.", symbol)
            return _empty

        # Deduplicate by href, drop ad/redirect URLs.
        seen: dict[str, str] = {}
        for r in results:
            href = r.get("href", "")
            body = r.get("body", "")
            if body and href not in seen and not _is_ad_url(href):
                seen[href] = body

        if not seen:
            logger.warning("%s: all results filtered out (ads/empty).", symbol)
            return _empty

        combined = " ".join(seen.values())
        summary  = _truncate_words(combined, 150)
        risks    = extract_risk_factors(combined)

        # Score each source individually, then average for the final score.
        source_scores = []
        for href, body in seen.items():
            s, l = extract_sentiment(body)
            source_scores.append({
                "url":             href,
                "snippet":         _truncate_words(body, 30),
                "sentiment_score": s,
                "sentiment_label": l,
            })

        avg_score = round(sum(ss["sentiment_score"] for ss in source_scores) / len(source_scores), 4)
        if avg_score > 0.15:
            label = "Positive"
        elif avg_score < -0.15:
            label = "Negative"
        else:
            label = "Neutral"

        return {
            "raw_summary":     summary,
            "sentiment_score": avg_score,
            "sentiment_label": label,
            "risk_factors":    risks,
            "sources":         list(seen.keys()),
            "source_scores":   source_scores,
        }

    except Exception as exc:
        logger.warning("%s: search_ticker failed unexpectedly: %s", symbol, exc)
        return _empty


def extract_sentiment(text: str) -> tuple:
    """Return (sentiment_score, label) from combined search text.

    score = (pos_count - neg_count) / max(pos_count + neg_count, 1)
    label = "Positive" | "Neutral" | "Negative"
    """
    t = text.lower()
    pos = sum(1 for w in POSITIVE_WORDS if w in t)
    neg = sum(1 for w in NEGATIVE_WORDS if w in t)
    score = round((pos - neg) / max(pos + neg, 1), 4)
    if score > 0.15:
        label = "Positive"
    elif score < -0.15:
        label = "Negative"
    else:
        label = "Neutral"
    return score, label


def extract_risk_factors(text: str) -> list:
    """Scan text for named risk categories; return evidence sentences.

    Returns list of {"category": str, "evidence": str}.
    At most one entry per category (first keyword match wins).
    """
    t        = text.lower()
    sentences = _split_sentences(text)
    results  = []

    for category, keywords in RISK_CATEGORIES.items():
        for kw in keywords:
            if kw in t:
                evidence = _find_evidence(sentences, kw)
                results.append({"category": category, "evidence": evidence})
                break   # one entry per category

    return results


# ---------------------------------------------------------------------------
# Query building
# ---------------------------------------------------------------------------

def _build_queries(symbol: str, name: str) -> list:
    is_cad    = symbol.upper().endswith(".TO")
    news_part = f"{_SITES_NEWS} OR {_SITES_CANADIAN}" if is_cad else _SITES_NEWS

    return [
        f"{symbol} {name} analyst rating price target 2026 {_SITES_ANALYST}",
        f"{symbol} stock forecast buy sell recommendation {_SITES_RETAIL} OR {_SITES_ANALYST}",
        f"{symbol} {name} risk factors concerns 2026 {news_part}",
    ]


# ---------------------------------------------------------------------------
# Search execution
# ---------------------------------------------------------------------------

def _run_query(query: str, use_cache: bool = True) -> list:
    """Return search results for query, using cache when available.

    Cache hit → return immediately (stable across runs within 24 h).
    Cache miss → try DDGS, fall back to HTTP, then persist the result.

    use_cache=False skips the read but still writes, so a caller forcing a
    refresh repopulates the cache for everyone else.
    """
    if use_cache and _cache is not None:
        cached = _cache.get(query)
        if cached is not None:
            logger.debug("Cache hit for query: %s", query[:60])
            return cached

    # Only real network calls pay the politeness delay.
    time.sleep(_QUERY_DELAY)

    results: list = []
    try:
        results = _search_ddgs(query)
    except Exception as exc:
        logger.debug("DDGS failed (%s); trying HTTP fallback.", exc)

    if not results:
        try:
            results = _search_http(query)
        except Exception as exc:
            logger.warning("Both search methods failed for query: %s — %s", query[:60], exc)

    # Empty results are cached too — otherwise a query that legitimately returns
    # nothing is re-run in full on every future run, forever.
    if _cache is not None:
        _cache.put(query, results)

    return results


def _search_ddgs(query: str) -> list:
    from ddgs import DDGS
    results = list(DDGS().text(query, max_results=3))
    return [
        {"body": r.get("body", ""), "href": r.get("href", "")}
        for r in results if r.get("body")
    ]


def _search_http(query: str) -> list:
    resp = _SESSION.get(
        "https://html.duckduckgo.com/html/",
        params={"q": query},
        timeout=10,
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    items = []
    for div in soup.select("div.result")[:3]:
        a_title   = div.select_one("a.result__a")
        a_snippet = div.select_one("a.result__snippet")
        if not a_snippet:
            continue
        href = a_title["href"] if a_title and a_title.get("href") else ""
        items.append({
            "body": a_snippet.get_text(separator=" ", strip=True),
            "href": href,
        })
    return items


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

_AD_URL_PATTERNS = ("/aclick", "/aclk", "bing.com/ck/", "doubleclick", "googleadservices")

def _is_ad_url(url: str) -> bool:
    return any(p in url for p in _AD_URL_PATTERNS)


def _split_sentences(text: str) -> list:
    """Split text into sentences on . ! ? boundaries."""
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _find_evidence(sentences: list, keyword: str) -> str:
    """Return the first sentence containing keyword, truncated to ≤20 words."""
    kw_lower = keyword.lower()
    for sent in sentences:
        if kw_lower in sent.lower():
            words = sent.split()
            return " ".join(words[:20]) + ("..." if len(words) > 20 else "")
    return keyword   # fallback: return keyword itself


def _truncate_words(text: str, max_words: int) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + "..."


# ---------------------------------------------------------------------------
# CLI demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    import pprint
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

    symbols_names = {
        "AAPL":  "Apple",
        "RY.TO": "Royal Bank of Canada",
        "MSFT":  "Microsoft",
        "ENB.TO": "Enbridge",
    }

    targets = sys.argv[1:] if len(sys.argv) > 1 else list(symbols_names.keys())

    for sym in targets:
        name = symbols_names.get(sym, sym)
        print(f"\n{'='*60}")
        print(f"  {sym}  |  {name}")
        print(f"{'='*60}")
        result = search_ticker(sym, name)
        print(f"  Sentiment      : {result['sentiment_label']}  ({result['sentiment_score']:+.3f})")
        print(f"  Summary        : {(result['raw_summary'] or 'N/A')[:120]}...")
        print(f"  Risk factors   : {len(result['risk_factors'])}")
        for rf in result["risk_factors"]:
            print(f"    [{rf['category']}]  {rf['evidence']}")
        print(f"  Sources        : {len(result['sources'])}")
        print(f"  {'URL':<70}  {'Score':>7}  Label")
        print(f"  {'-'*70}  {'-'*7}  -----")
        for ss in result["source_scores"]:
            url_display = ss["url"][:68] if len(ss["url"]) > 68 else ss["url"]
            print(f"  {url_display:<70}  {ss['sentiment_score']:>+7.3f}  {ss['sentiment_label']}")
            print(f"    snippet: {ss['snippet']}")
