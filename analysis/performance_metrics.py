"""
Performance metrics module — powers the UI's "Performance Analysis" tab.

Public API:
    compute_performance_metrics(ticker_data) -> dict

Usage:
    from analysis.performance_metrics import compute_performance_metrics
    perf = compute_performance_metrics(ticker_data["AAPL"])
"""

import logging

from analysis.risk_scorer import _de_ratio
from data.ticker_selector import DISPLAY_NAMES, SECTOR_MAP

logger = logging.getLogger(__name__)


def compute_performance_metrics(ticker_data: dict) -> dict:
    """Return a flat performance-ratio dict for one ticker.

    Args:
        ticker_data: Dict as returned by DataFetcher.fetch_ticker()/fetch_all().

    Returns:
        Dict with symbol/name/sector/is_etf plus ratio fields. Fields that
        can't be computed (missing data, ETF with no income statement, etc.)
        are None rather than raising.
    """
    symbol      = ticker_data["symbol"]
    is_etf      = ticker_data["is_etf"]
    info        = ticker_data.get("info") or {}
    income_stmt = ticker_data.get("income_stmt")

    name   = info.get("longName") or DISPLAY_NAMES.get(symbol, symbol)
    sector = info.get("sector")   or SECTOR_MAP.get(symbol)

    ebit          = _find_value(income_stmt, "ebit", exclude="ebitda")
    interest_exp  = _find_value(income_stmt, "interest expense")

    return {
        "symbol":             symbol,
        "name":               name,
        "sector":             sector,
        "is_etf":             is_etf,
        "pe":                 info.get("trailingPE"),
        "forward_pe":         info.get("forwardPE"),
        "peg_ratio":          info.get("trailingPegRatio") or info.get("pegRatio"),
        "debt_equity":        _de_ratio(info),
        "quick_ratio":        info.get("quickRatio"),
        "current_ratio":      info.get("currentRatio"),
        "roe":                info.get("returnOnEquity"),
        "roa":                info.get("returnOnAssets"),
        "interest_coverage":  _safe_div(ebit, interest_exp),
        "revenue_cagr_3y":    _cagr_3y(_find_row(income_stmt, "total revenue")),
        "eps_cagr_3y":        _eps_cagr_3y(income_stmt),
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _find_row(df, *labels, exclude: str | None = None):
    """Return the first matching row (as a list of values, most-recent first),
    or None. Columns in yfinance statements are ordered most-recent-first."""
    if df is None or df.empty:
        return None
    for label in labels:
        matches = [
            r for r in df.index
            if label.lower() in str(r).lower()
            and (exclude is None or exclude.lower() not in str(r).lower())
        ]
        if matches:
            # Prefer an exact match over a longer row name that merely contains it.
            exact = [r for r in matches if str(r).lower() == label.lower()]
            row = df.loc[exact[0] if exact else matches[0]]
            return list(row.values)
    return None


def _find_value(df, *labels, exclude: str | None = None):
    """Latest (most-recent column) scalar for the first matching row, or None."""
    row = _find_row(df, *labels, exclude=exclude)
    if not row:
        return None
    for v in row:
        if v is not None and not _is_nan(v):
            return float(v)
    return None


def _is_nan(v) -> bool:
    try:
        return v != v  # NaN is the only value that is not equal to itself
    except Exception:
        return False


def _safe_div(numerator, denominator):
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _cagr_3y(row) -> float | None:
    """3-year CAGR from a most-recent-first row of ≥4 annual values."""
    if not row or len(row) < 4:
        return None
    recent, three_yr_ago = row[0], row[3]
    if recent is None or three_yr_ago is None or _is_nan(recent) or _is_nan(three_yr_ago):
        return None
    if recent <= 0 or three_yr_ago <= 0:
        return None
    return (float(recent) / float(three_yr_ago)) ** (1 / 3) - 1


def _eps_cagr_3y(income_stmt) -> float | None:
    row = _find_row(income_stmt, "diluted eps") or _find_row(income_stmt, "basic eps")
    return _cagr_3y(row)


# ---------------------------------------------------------------------------
# CLI demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    import logging as _log
    _log.basicConfig(level=logging.WARNING)

    from data.fetcher import DataFetcher

    symbols = sys.argv[1:] if len(sys.argv) > 1 else ["AAPL", "RY.TO", "XIU.TO"]
    fetcher = DataFetcher()
    raw = fetcher.fetch_all(symbols)

    for sym, d in raw.items():
        perf = compute_performance_metrics(d)
        print(f"\n── {sym} ({'ETF' if d['is_etf'] else 'Stock'}) ──────────────────────────")
        for k, v in perf.items():
            print(f"  {k:<20}  {v}")
