"""
Macro-Aware Analysis (Step 7, §12).

Fetches current macro context via two DuckDuckGo searches and maps it to
sector score modifiers applied during recommendation ranking.

Public API:
    fetch_macro_context()           -> dict  (§12.3 output)
    get_sector_modifiers(context)   -> dict  {sector: float}

Usage:
    from analysis.macro import fetch_macro_context, get_sector_modifiers
    ctx = fetch_macro_context()
    mods = get_sector_modifiers(ctx)

    # Standalone demo
    python -c "from analysis.macro import macro_demo; macro_demo()"
"""

import logging
import re
import time

logger = logging.getLogger(__name__)

try:
    import cache as _cache
except ImportError:
    _cache = None  # type: ignore[assignment]

# Politeness delay applied per real network query (never on a cache hit).
_QUERY_DELAY = 0.5

# ---------------------------------------------------------------------------
# §12.2  Sector sensitivity matrix
# Tuple order: (rates_rising, rates_falling, high_inflation, recession)
# ---------------------------------------------------------------------------

SECTOR_SENSITIVITY: dict[str, tuple] = {
    "Financials":             ( 0.5, -0.3,  0.2, -0.5),
    "Bond ETF":               (-0.8,  0.8, -0.5,  0.3),
    "Diversified ETF":        (-0.1,  0.1, -0.1, -0.2),
    "Technology":             (-0.3,  0.4, -0.2, -0.4),
    "Energy":                 ( 0.3, -0.1,  0.7, -0.5),
    "Consumer Staples":       ( 0.0,  0.1,  0.2,  0.6),
    "Healthcare":             ( 0.1,  0.1,  0.0,  0.5),
    "Industrials":            (-0.1,  0.2,  0.0, -0.4),
    "Consumer Discretionary": (-0.2,  0.3, -0.4, -0.6),
    "Commodities ETF":        ( 0.2, -0.1,  0.8, -0.3),
    "Materials":              ( 0.3, -0.1,  0.8, -0.5),
    "Utilities":              (-0.4,  0.4,  0.1,  0.4),
    "Real Estate":            (-0.5,  0.5,  0.2, -0.3),
    "Telecommunications":     (-0.2,  0.2,  0.0, -0.1),
}

# ---------------------------------------------------------------------------
# Classification keywords
# ---------------------------------------------------------------------------

_RATE_RISING_KEYWORDS  = ["hiked", "hike", "rising rate", "raised rate", "rate increase",
                           "higher rate", "tightening", "hawkish", "rate hike"]
_RATE_FALLING_KEYWORDS = ["cut rate", "rate cut", "cutting rate", "eased", "easing",
                           "lowered rate", "falling rate", "dovish", "rate reduction"]
_RECESSION_KEYWORDS    = ["recession", "contraction", "negative growth", "gdp fell",
                          "gdp decline", "downturn", "economic contraction"]
_EXPANDING_KEYWORDS    = ["expanding", "economic growth", "accelerating", "robust economy",
                          "gdp grew", "gdp rose", "gdp growth", "strong growth"]
_SLOWING_KEYWORDS      = ["slowing", "slowdown", "weakening", "moderating", "cooling economy"]
_HIGH_INFLATION_KWORDS = ["high inflation", "elevated inflation", "inflation surge",
                          "inflation above target", "runaway inflation"]
_LOW_INFLATION_KWORDS  = ["low inflation", "below target", "deflation", "disinflation",
                          "inflation subdued"]

# Capture a bare percentage near the word "inflation", e.g. "inflation of 3.2%"
_INFLATION_PCT_RE = re.compile(r"inflation\D{0,30}?(\d+\.?\d*)\s*%", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_macro_context() -> dict:
    """Fetch current macro regime and return §12.3 output dict. Never raises.

    Returns defaults ("stable"/"moderate"/"expanding") with data_unavailable=True
    if the searches fail or return no usable text.
    """
    try:
        snippets = _run_macro_searches()
        combined = " ".join(snippets).strip()
        if not combined:
            logger.warning("macro: searches returned no text — using defaults.")
            return _build_output("stable", "moderate", "expanding", data_unavailable=True)

        rate_env  = _classify_rate_environment(combined)
        inflation = _classify_inflation(combined)
        momentum  = _classify_momentum(combined)
        return _build_output(rate_env, inflation, momentum, data_unavailable=False)

    except Exception as exc:
        logger.warning("fetch_macro_context failed: %s", exc)
        return _build_output("stable", "moderate", "expanding", data_unavailable=True)


def get_sector_modifiers(context: dict) -> dict:
    """Derive per-sector score modifiers from a macro context dict.

    Active conditions (rates_rising, rates_falling, high_inflation, recession)
    are detected from context and their SECTOR_SENSITIVITY column values are
    summed.  Multiple conditions can apply simultaneously (e.g. rising rates
    AND high inflation).

    The caller is responsible for clamping quality_score to [0.5, 5.0] after
    adding the modifier (§12.2).

    Args:
        context: Output of fetch_macro_context().

    Returns:
        Dict mapping every sector name → combined float modifier.
    """
    rate_env       = context.get("rate_environment",  "stable")
    inflation      = context.get("inflation_level",   "moderate")
    momentum       = context.get("economic_momentum", "expanding")

    rates_rising   = rate_env  == "rising"
    rates_falling  = rate_env  == "falling"
    high_inflation = inflation == "high"
    in_recession   = momentum  == "recession"

    result: dict[str, float] = {}
    for sector, (r_up, r_down, inf_h, rec) in SECTOR_SENSITIVITY.items():
        mod = 0.0
        if rates_rising:
            mod += r_up
        if rates_falling:
            mod += r_down
        if high_inflation:
            mod += inf_h
        if in_recession:
            mod += rec
        result[sector] = round(mod, 2)
    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _run_macro_searches() -> list:
    queries = [
        "Bank of Canada interest rate 2026 current",
        "Canada inflation rate GDP growth 2026",
    ]
    snippets: list[str] = []
    for q in queries:
        # Delay lives inside _ddg_search, after the cache check.
        snippets.extend(_ddg_search(q))
    return snippets


def _ddg_search(query: str) -> list:
    """Run one DDG query with caching; return up to 3 snippet strings. Never raises."""
    if _cache is not None:
        cached = _cache.get(query)
        if cached is not None:
            logger.debug("Cache hit for macro query: %s", query[:60])
            return cached

    # Only real network calls pay the politeness delay.
    time.sleep(_QUERY_DELAY)

    snippets: list = []

    # Preferred: ddgs package (stable, no HTML parsing)
    try:
        from ddgs import DDGS
        results = list(DDGS().text(query, max_results=3))
        snippets = [r.get("body", "") for r in results if r.get("body")]
    except Exception:
        pass

    # Fallback: HTML scraping
    if not snippets:
        try:
            import requests
            from bs4 import BeautifulSoup
            url = "https://html.duckduckgo.com/html/"
            resp = requests.get(
                url,
                params={"q": query},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10,
            )
            soup = BeautifulSoup(resp.text, "html.parser")
            snippets = [el.get_text() for el in soup.select("a.result__snippet")][:3]
        except Exception as exc:
            logger.warning("macro DDG fallback failed: %s", exc)

    # Empty results are cached too (on a shorter TTL) so a failing query isn't
    # re-run in full on every future run.
    if _cache is not None:
        _cache.put(query, snippets)

    return snippets


def _classify_rate_environment(text: str) -> str:
    t = text.lower()
    rising  = sum(1 for k in _RATE_RISING_KEYWORDS  if k in t)
    falling = sum(1 for k in _RATE_FALLING_KEYWORDS if k in t)
    if rising > falling:
        return "rising"
    if falling > rising:
        return "falling"
    return "stable"


def _classify_inflation(text: str) -> str:
    t = text.lower()
    # Try numeric extraction first
    matches = _INFLATION_PCT_RE.findall(t)
    if matches:
        try:
            pct = float(matches[0])
            if pct > 3.0:
                return "high"
            if pct >= 2.0:
                return "moderate"
            return "low"
        except ValueError:
            pass

    # Keyword fallback
    high_hits = sum(1 for k in _HIGH_INFLATION_KWORDS if k in t)
    low_hits  = sum(1 for k in _LOW_INFLATION_KWORDS  if k in t)
    if high_hits > low_hits:
        return "high"
    if low_hits > high_hits:
        return "low"
    return "moderate"


def _classify_momentum(text: str) -> str:
    t = text.lower()
    rec_hits  = sum(1 for k in _RECESSION_KEYWORDS if k in t)
    exp_hits  = sum(1 for k in _EXPANDING_KEYWORDS if k in t)
    slow_hits = sum(1 for k in _SLOWING_KEYWORDS   if k in t)

    if rec_hits > exp_hits and rec_hits > slow_hits:
        return "recession"
    if exp_hits >= slow_hits and exp_hits > rec_hits:
        return "expanding"
    return "slowing"


def _regime_description(rate_env: str, inflation: str, momentum: str) -> str:
    """One-line plain-English regime description with a sector implication tail."""
    rate_phrase = {"rising": "rates rising", "falling": "rates falling"}.get(rate_env, "rates stable")
    inf_phrase  = {"high": "high inflation", "low": "low inflation"}.get(inflation, "moderate inflation")
    mom_phrase  = {"recession": "recessionary conditions", "slowing": "slowing growth"}.get(momentum, "expanding economy")

    if rate_env == "rising" and inflation == "high":
        tail = "favours Financials, Energy, Commodities; headwind for Bonds and Real Estate."
    elif rate_env == "falling":
        tail = "favours Bonds, Real Estate, Utilities, and Technology."
    elif momentum == "recession":
        tail = "favours Consumer Staples, Healthcare, and Utilities."
    elif momentum == "slowing":
        tail = "favours defensive sectors; caution on Discretionary and Technology."
    else:
        tail = "broadly supportive of equities."

    return f"{rate_phrase.capitalize()}, {inf_phrase}, {mom_phrase} — {tail}"


def _build_output(rate_environment: str, inflation_level: str,
                  economic_momentum: str, data_unavailable: bool = False) -> dict:
    ctx = {
        "rate_environment":  rate_environment,
        "inflation_level":   inflation_level,
        "economic_momentum": economic_momentum,
    }
    modifiers = get_sector_modifiers(ctx)

    if data_unavailable:
        description = "Macro data unavailable — using neutral defaults."
    else:
        description = _regime_description(rate_environment, inflation_level, economic_momentum)

    return {
        "rate_environment":   rate_environment,
        "inflation_level":    inflation_level,
        "economic_momentum":  economic_momentum,
        "regime_description": description,
        "sector_modifiers":   modifiers,
        "data_unavailable":   data_unavailable,
    }


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

def macro_demo() -> None:
    """Fetch live macro context and print results. Run as a module standalone."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    ctx = fetch_macro_context()
    print(f"\nRate environment : {ctx['rate_environment']}")
    print(f"Inflation level  : {ctx['inflation_level']}")
    print(f"Economic momentum: {ctx['economic_momentum']}")
    print(f"Data unavailable : {ctx['data_unavailable']}")
    print(f"\nRegime: {ctx['regime_description']}\n")
    print("Sector modifiers:")
    for sector, mod in ctx["sector_modifiers"].items():
        bar = "+" if mod > 0 else ""
        print(f"  {sector:<25} {bar}{mod:.2f}")
