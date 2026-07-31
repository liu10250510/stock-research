"""Market Summary — what happened today, this week, and this month.

Two layers with very different reliability, kept deliberately separate:

  * Quantitative backbone — index, commodity, rate and FX moves computed from
    yfinance price history. These are facts and are always present.
  * News layer — headlines scraped via DuckDuckGo and summarised into prose.
    Best-effort: sources go stale, get paywalled, or return nothing. When that
    happens the window is flagged `data_unavailable` and the caller shows the
    numbers without a narrative, rather than inventing one.

Public API:
    fetch_market_summary(include_news=True, use_cache=True) -> dict

Usage:
    from analysis.market_summary import fetch_market_summary
    summary = fetch_market_summary()

    python -c "from analysis.market_summary import market_summary_demo; market_summary_demo()"
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Instrument universe
# ---------------------------------------------------------------------------
#
# These are passed straight to DataFetcher.fetch_all — deliberately NOT added to
# data/ticker_selector.TICKER_POOL, where they would leak into select_tickers()
# and become recommendable "picks".
#
# `kind` matters for correctness: an equity index has a percentage return, but a
# bond yield or a volatility index is a *level*. Reporting "the 10-year returned
# +2.1%" would be meaningless — those report a level and an absolute change.

_INSTRUMENTS = [
    # symbol,      display name,          group,     kind,    unit
    ("^GSPTSE",   "S&P/TSX Composite",    "Canada",  "index", "pct"),
    ("^GSPC",     "S&P 500",              "US",      "index", "pct"),
    ("^IXIC",     "Nasdaq Composite",     "US",      "index", "pct"),
    ("^FTSE",     "FTSE 100",             "Global",  "index", "pct"),
    ("CL=F",      "Crude oil (WTI)",      "Global",  "commodity", "pct"),
    ("GC=F",      "Gold",                 "Global",  "commodity", "pct"),
    ("^TNX",      "US 10-year yield",     "Global",  "rate",  "level"),
    ("CAD=X",     "USD/CAD",              "Global",  "fx",    "pct"),
    ("^VIX",      "Volatility (VIX)",     "Global",  "vol",   "level"),
]

_SYMBOLS = [row[0] for row in _INSTRUMENTS]
_META    = {row[0]: {"name": row[1], "group": row[2], "kind": row[3], "unit": row[4]}
            for row in _INSTRUMENTS}

# Window key -> (human label, technical.py return key)
_WINDOWS = {
    "1d": ("Today",      "return_1d"),
    "1w": ("Past week",  "return_1w"),
    "1m": ("Past month", "return_1m"),
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_market_summary(include_news: bool = True, use_cache: bool = True) -> dict:
    """Return the §Market Summary output dict. Never raises.

    Args:
        include_news: When False, skip the news layer entirely and return only
                      the quantitative backbone. Useful for testing and for
                      callers that only want the numbers.
        use_cache:    Passed through to the data and search caches. False forces
                      a fresh fetch on both.

    Returns:
        {
          "as_of":   ISO-8601 UTC timestamp,
          "windows": {"1d"|"1w"|"1m": {...}},
          "errors":  list[str],
        }
    """
    from analysis.sectors import SECTOR_SYMBOLS, build_sectors, compute_trends

    errors: list[str] = []

    # One batched fetch for instruments and sector ETFs together — 30 symbols
    # through a single thread pool rather than two sequential rounds.
    raw, fetch_errors = _fetch_raw(_SYMBOLS + SECTOR_SYMBOLS, use_cache=use_cache)
    errors.extend(fetch_errors)

    moves_by_window, move_errors = _build_moves(raw)
    errors.extend(move_errors)

    try:
        sector_windows = build_sectors(raw, _WINDOWS)
        trends = compute_trends(sector_windows)
    except Exception as exc:
        logger.warning("Sector analysis failed: %s", exc)
        errors.append(f"sectors: {exc}")
        sector_windows, trends = {k: [] for k in _WINDOWS}, {}

    windows = {}
    for key, (label, _) in _WINDOWS.items():
        windows[key] = {
            "label":            label,
            "sectors":          sector_windows.get(key, []),
            "trends":           trends.get(key, {}),
            "moves":            moves_by_window.get(key, {}),
            "narrative":        None,
            "summarizer":       None,
            "headlines":        [],
            "sources":          [],
            "sentiment_score":  0.0,
            "sentiment_label":  "Neutral",
            "data_unavailable": True,
        }

    if include_news:
        cross = trends.get("_cross", {})
        for key in _WINDOWS:
            try:
                _attach_news(windows[key], key, cross, use_cache=use_cache)
            except Exception as exc:                     # never let news break the numbers
                logger.warning("Market news failed for window %s: %s", key, exc)
                errors.append(f"news:{key}: {exc}")

    return {
        "as_of":   datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "windows": windows,
        "cross_window_trends": trends.get("_cross", {}),
        "errors":  errors,
    }


# ---------------------------------------------------------------------------
# Quantitative backbone
# ---------------------------------------------------------------------------

def _fetch_raw(symbols: list, use_cache: bool = True) -> tuple[dict, list[str]]:
    """Fetch every symbol in one batch.

    Routed through DataFetcher so this inherits the thread pool, the 15-minute
    market cache, and the retry behaviour rather than reimplementing them.
    """
    from data.fetcher import DataFetcher

    try:
        raw = DataFetcher(use_cache=use_cache).fetch_all(symbols)
    except Exception as exc:
        logger.warning("Market data fetch failed: %s", exc)
        return {}, [f"fetch: {exc}"]

    missing = [s for s in symbols if s not in raw]
    return raw, (["unavailable: " + ", ".join(missing)] if missing else [])


def _build_moves(raw: dict) -> tuple[dict, list[str]]:
    """Return ({window: {symbol: move}}, errors) for the index/macro instruments."""
    from analysis.technical import compute_technicals

    errors: list[str] = []
    by_window: dict[str, dict] = {k: {} for k in _WINDOWS}

    for symbol in _SYMBOLS:
        data = raw.get(symbol)
        if not data:
            continue
        meta = _META[symbol]
        try:
            hist = data.get("history")
            if hist is None or hist.empty:
                continue
            tech  = compute_technicals(hist)
            close = hist["Close"].dropna()
            last  = float(close.iloc[-1]) if not close.empty else None

            for wkey, (_, ret_key) in _WINDOWS.items():
                ret = tech.get(ret_key)
                entry = {
                    "symbol":   symbol,
                    "name":     meta["name"],
                    "group":    meta["group"],
                    "kind":     meta["kind"],
                    "unit":     meta["unit"],
                    "level":    round(last, 4) if last is not None else None,
                    "return":   ret,
                    "change":   None,
                }
                # A yield or a volatility index is a level, not a price: report the
                # absolute move so nobody reads "+2.1%" as a return on capital.
                if meta["unit"] == "level" and ret is not None and last is not None:
                    prior = last / (1 + ret) if (1 + ret) != 0 else None
                    if prior is not None:
                        entry["change"] = round(last - prior, 4)
                    entry["return"] = None
                by_window[wkey][symbol] = entry
        except Exception as exc:
            logger.debug("Move computation failed for %s: %s", symbol, exc)
            errors.append(f"{symbol}: {exc}")

    return by_window, errors


# ---------------------------------------------------------------------------
# News layer
# ---------------------------------------------------------------------------

def _attach_news(window: dict, window_key: str, cross_trends: dict,
                 use_cache: bool = True) -> None:
    """Populate the news fields on *window* in place. Leaves them alone on failure."""
    from research.news import fetch_market_news
    from research.llm_summarizer import summarize

    news = fetch_market_news(window_key, use_cache=use_cache)
    headlines = news.get("headlines") or []

    window["headlines"]       = headlines
    window["sources"]         = news.get("sources") or []
    window["sentiment_score"] = news.get("sentiment_score", 0.0)
    window["sentiment_label"] = news.get("sentiment_label", "Neutral")
    # Sector data is computed locally and doesn't depend on the network, so a
    # window with sectors but no news still has a real story to tell — only the
    # *cause* is missing. data_unavailable now means "no news", which the UI
    # reports separately from "no data at all".
    window["data_unavailable"] = not headlines

    narrative, engine = summarize(
        window_label=window["label"],
        moves=window["moves"],
        headlines=headlines,
        sectors=window.get("sectors") or [],
        trends=window.get("trends") or {},
        cross_trends=cross_trends,
    )
    window["narrative"]  = narrative
    window["summarizer"] = engine


# ---------------------------------------------------------------------------
# CLI demo
# ---------------------------------------------------------------------------

def market_summary_demo() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s  %(message)s")
    s = fetch_market_summary()

    print(f"\nMarket Summary — as of {s['as_of']}")
    if s["errors"]:
        print(f"  notes: {s['errors']}")

    cross = s.get("cross_window_trends") or {}
    if any(cross.values()):
        print("\n── Across all windows ──")
        for key, title in (("sustained_strength", "Positive in every window"),
                           ("sustained_weakness", "Negative in every window"),
                           ("rotating_in",  "Turned up after a weak month"),
                           ("rotating_out", "Turned down after a strong month")):
            items = cross.get(key) or []
            if items:
                names = ", ".join(f"{i['sector']} ({i['market']})" for i in items)
                print(f"  {title:32} {names}")

    for key, w in s["windows"].items():
        print(f"\n── {w['label']} ({key}) ──")

        t = w.get("trends") or {}
        if t.get("breadth_label"):
            print(f"  Breadth      : {t['breadth_label']} ({t.get('breadth',0):.0%} positive)")
        if t.get("risk_label"):
            print(f"  Positioning  : {t['risk_label']}")
        for d in t.get("divergences", []):
            print(f"  CA/US split  : {d['sector']}  CA {d['canada']:+.2%} vs US {d['us']:+.2%}")

        sectors = w.get("sectors") or []
        if sectors:
            print("  Sectors (best → worst):")
            for sec in sectors:
                print(f"    {sec['sector']:24} {sec['market']:7} {sec['return']:+8.2%}")

        print("  Backdrop:")
        for m in w["moves"].values():
            if m["unit"] == "level":
                chg = f"{m['change']:+.2f}" if m["change"] is not None else "—"
                print(f"    {m['name']:22} {m['level']:>10,.2f}  ({chg})")
            else:
                ret = f"{m['return']:+.2%}" if m["return"] is not None else "—"
                print(f"    {m['name']:22} {m['level']:>10,.2f}  {ret:>9}")

        if w["data_unavailable"]:
            print("  news: none returned — narrative built from sector data only")
        else:
            print(f"  tone: {w['sentiment_label']} | sources: {', '.join(w['sources'][:5])}")
        if w["narrative"]:
            print(f"  [{w['summarizer']}] {w['narrative']}")


if __name__ == "__main__":
    market_summary_demo()
