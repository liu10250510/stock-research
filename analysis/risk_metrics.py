"""
Risk-Adjusted Metrics module (Step 4, §8.4).  ★ Feature 1

Public API:
    compute_risk_metrics(ticker_data, return_1y=None) -> dict

Computes:
    Sharpe ratio   — return per unit of total risk
    Sortino ratio  — return per unit of downside risk
    Max drawdown   — worst peak-to-trough over 5yr history
    Annualized vol — 30-day std dev scaled to annual

Usage:
    from analysis.risk_metrics import compute_risk_metrics
    rm = compute_risk_metrics(data["AAPL"])
    rm = compute_risk_metrics(data["AAPL"], return_1y=technicals["return_1y"])
"""

import logging
import math

import pandas as pd

logger = logging.getLogger(__name__)

RISK_FREE_RATE = 0.035   # ~3.5% CAD overnight rate (BoC); surfaced on the PDF cover.
                         # One rate is applied to both CAD- and USD-denominated
                         # assets, which is an approximation.

# Trailing window for the Sharpe/Sortino denominators — matched to the 1-year
# return in their numerator.
_WINDOW_1Y = 252


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_risk_metrics(ticker_data: dict, return_1y: float | None = None) -> dict:
    """Compute Sharpe, Sortino, max drawdown, and annualised volatility.

    Args:
        ticker_data: Dict as returned by DataFetcher.fetch_ticker().
        return_1y:   Pre-computed 1-year return from technical.py.
                     If None, it is calculated here from history.

    Returns:
        Dict with sharpe_ratio, sortino_ratio, max_drawdown, annualized_vol.
        All values are None when there is insufficient history.
    """
    history = ticker_data.get("history")

    empty = {
        "sharpe_ratio":  None,
        "sortino_ratio": None,
        "max_drawdown":  None,
        "annualized_vol": None,
    }

    if history is None or history.empty:
        return empty

    close      = history["Close"].dropna()
    daily_ret  = close.pct_change().dropna()

    if len(daily_ret) < 30:
        return empty

    # Sharpe and Sortino both use a 1-year return in the numerator, so both
    # denominators are measured over the same trailing year. Previously the
    # volatility came from a 30-day window and the downside deviation from the
    # full 5-year history — three different periods in one table, which left
    # e.g. XIU.TO's Sharpe overstated by ~35%.
    ret_1y      = return_1y if return_1y is not None else _return_1y(close)
    window      = daily_ret.tail(_WINDOW_1Y)
    ann_vol     = _annualized_vol(window)

    sharpe  = _sharpe(ret_1y, ann_vol)
    sortino = _sortino(ret_1y, window)
    max_dd  = _max_drawdown(daily_ret)   # drawdown intentionally spans all history

    return {
        "sharpe_ratio":  sharpe,
        "sortino_ratio": sortino,
        "max_drawdown":  max_dd,
        "annualized_vol": ann_vol,
    }


# ---------------------------------------------------------------------------
# §8.4  Metric calculations
# ---------------------------------------------------------------------------

def _annualized_vol(daily_ret: pd.Series) -> float | None:
    """Annualized volatility of the supplied return series (no further slicing)."""
    if len(daily_ret) < 2:
        return None
    vol = float(daily_ret.std()) * math.sqrt(252)
    return round(vol, 6) if not math.isnan(vol) else None


def _return_1y(close: pd.Series) -> float | None:
    if len(close) < 252:
        return None
    past = float(close.iloc[-252])
    last = float(close.iloc[-1])
    return (last / past) - 1 if past != 0 else None


def _sharpe(ret_1y, ann_vol) -> float | None:
    if ret_1y is None or ann_vol is None or ann_vol == 0:
        return None
    return round((ret_1y - RISK_FREE_RATE) / ann_vol, 4)


def _sortino(ret_1y, daily_ret: pd.Series) -> float | None:
    if ret_1y is None:
        return None
    downside = daily_ret[daily_ret < 0]
    if len(downside) < 2:
        return None
    downside_dev = float(downside.std()) * math.sqrt(252)
    if downside_dev == 0:
        return None
    return round((ret_1y - RISK_FREE_RATE) / downside_dev, 4)


def _max_drawdown(daily_ret: pd.Series) -> float | None:
    """Max drawdown from an already-computed daily return series."""
    if len(daily_ret) < 1:
        return None
    cum_ret     = (1 + daily_ret).cumprod()
    rolling_max = cum_ret.cummax()
    drawdown    = (cum_ret - rolling_max) / rolling_max
    dd = float(drawdown.min())
    return round(dd, 6) if not math.isnan(dd) else None


# ---------------------------------------------------------------------------
# CLI demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    import logging as _log
    _log.basicConfig(level=logging.WARNING)

    from data.fetcher import DataFetcher
    from analysis.technical import compute_technicals

    symbols = sys.argv[1:] if len(sys.argv) > 1 else ["AAPL", "RY.TO", "XIU.TO", "ZAG.TO"]
    fetcher = DataFetcher()
    raw = fetcher.fetch_all(symbols)

    print(f"\n{'Symbol':<10} {'Sharpe':>8} {'Sortino':>9} {'MaxDD':>9} {'AnnVol':>8}")
    print("-" * 50)
    for sym, d in raw.items():
        tech = compute_technicals(d["history"])
        rm   = compute_risk_metrics(d, return_1y=tech["return_1y"])
        def _f(v): return f"{v:+.3f}" if v is not None else "   N/A"
        print(f"{sym:<10} {_f(rm['sharpe_ratio']):>8} {_f(rm['sortino_ratio']):>9} "
              f"{_f(rm['max_drawdown']):>9} {_f(rm['annualized_vol']):>8}")
