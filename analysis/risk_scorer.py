"""
Risk Scoring module (Step 4, §8).

Public API:
    score_ticker(ticker_data, spy_history=None) -> dict

Six components weighted differently for stocks vs ETFs:
    Beta            stocks 30%  ETFs 45%
    Volatility      stocks 25%  ETFs 45%
    Debt/Equity     stocks 15%  ETFs  0%
    P/E Ratio       stocks 10%  ETFs  0%
    Market Cap      stocks 10%  ETFs 10%
    Profit Margin   stocks 10%  ETFs  0%

Usage:
    from analysis.risk_scorer import score_ticker, score_all_demo
    result = score_ticker(data["AAPL"], spy_history=fetcher.spy_history)
    score_all_demo()
"""

import logging
import math

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Component weights
# ---------------------------------------------------------------------------

STOCK_WEIGHTS = {
    "beta":          0.30,
    "volatility":    0.25,
    "debt_equity":   0.15,
    "pe_ratio":      0.10,
    "market_cap":    0.10,
    "profit_margin": 0.10,
}

ETF_WEIGHTS = {
    "beta":          0.45,
    "volatility":    0.45,
    "debt_equity":   0.00,
    "pe_ratio":      0.00,
    "market_cap":    0.10,
    "profit_margin": 0.00,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def score_ticker(ticker_data: dict, spy_history=None) -> dict:
    """Compute risk score (1–10) and component breakdown for one ticker.

    Args:
        ticker_data:  Dict as returned by DataFetcher.fetch_ticker().
        spy_history:  1-yr Close Series from DataFetcher.spy_history;
                      used only when info['beta'] is missing.

    Returns:
        {"risk_score": float, "components": {name: score, ...}}
    """
    info    = ticker_data.get("info") or {}
    history = ticker_data.get("history")
    is_etf  = ticker_data.get("is_etf", False)
    weights = ETF_WEIGHTS if is_etf else STOCK_WEIGHTS

    beta     = _get_beta(info, history, spy_history)
    vol      = _annualized_vol_30d(history)
    de       = _de_ratio(info)
    pe       = info.get("trailingPE")
    mcap     = info.get("totalAssets") if is_etf else info.get("marketCap")
    margin   = info.get("profitMargins")

    components = {
        "beta":          _score_beta(beta),
        "volatility":    _score_volatility(vol),
        "debt_equity":   _score_de(de),
        "pe_ratio":      _score_pe(pe),
        "market_cap":    _score_mcap(mcap),
        "profit_margin": _score_margin(margin),
    }

    final = sum(components[k] * weights[k] for k in components)
    final = round(max(1.0, min(10.0, final)), 2)

    # beta is returned so compute_fundamentals can reuse it instead of repeating
    # _calc_beta, which is the expensive path whenever info['beta'] is missing.
    return {"risk_score": final, "components": components, "beta": beta}


# ---------------------------------------------------------------------------
# §8.1  Component scoring functions
# ---------------------------------------------------------------------------

def _score_beta(beta) -> float:
    if beta is None:
        return 6.0   # neutral fallback when beta unavailable
    b = float(beta)
    if b <= 0.5:  return 1.5
    if b <= 0.8:  return 3.0
    if b <= 1.0:  return 4.5
    if b <= 1.2:  return 6.0
    if b <= 1.5:  return 7.5
    return 9.5


def _score_volatility(vol) -> float:
    if vol is None:
        return 5.0   # neutral fallback
    v = float(vol)
    if v <= 0.10: return 1.5
    if v <= 0.15: return 3.0
    if v <= 0.20: return 5.0
    if v <= 0.30: return 7.0
    return 9.0


def _score_de(de) -> float:
    if de is None:
        return 8.5
    d = float(de)
    if d <= 0.3: return 1.5
    if d <= 0.7: return 3.5
    if d <= 1.5: return 5.5
    if d <= 3.0: return 7.5
    return 8.5


def _score_pe(pe) -> float:
    if pe is None:
        return 7.0
    p = float(pe)
    if p < 0:    return 8.0
    if p <= 15:  return 2.0
    if p <= 25:  return 4.0
    if p <= 40:  return 6.0
    if p <= 60:  return 8.0
    return 10.0


def _score_mcap(mcap) -> float:
    if mcap is None:
        return 9.0
    m = float(mcap)
    if m >= 200e9: return 1.5
    if m >= 50e9:  return 3.0
    if m >= 10e9:  return 5.0
    if m >= 2e9:   return 7.0
    return 9.0


def _score_margin(margin) -> float:
    if margin is None:
        return 9.0
    m = float(margin)
    if m >= 0.20: return 2.0
    if m >= 0.10: return 3.5
    if m >= 0.05: return 5.0
    if m >= 0.0:  return 6.5
    return 9.0


# ---------------------------------------------------------------------------
# Beta calculation helpers
# ---------------------------------------------------------------------------

def _get_beta(info: dict, history, spy_history, symbol: str = "") -> float | None:
    beta = info.get("beta")
    if beta is not None:
        return float(beta)
    return _calc_beta(history, spy_history, symbol)


_SPY_RET_CACHE: tuple | None = None


def _spy_returns(spy_history):
    """Trailing-252d SPY returns, memoised on the baseline Series identity.

    The SPY baseline is a single invariant Series shared across the whole
    universe, but this was previously re-differenced once per ticker.
    """
    global _SPY_RET_CACHE
    if _SPY_RET_CACHE is not None and _SPY_RET_CACHE[0] is spy_history:
        return _SPY_RET_CACHE[1]
    rets = spy_history.pct_change().dropna().tail(252)
    _SPY_RET_CACHE = (spy_history, rets)
    return rets


def _calc_beta(history, spy_history, symbol: str = "") -> float | None:
    if spy_history is None or history is None or history.empty:
        return None
    try:
        close      = history["Close"].dropna()
        ticker_ret = close.pct_change().dropna().tail(252)
        spy_ret    = _spy_returns(spy_history)
        common     = ticker_ret.index.intersection(spy_ret.index)
        if len(common) < 30:
            return None
        t = ticker_ret.loc[common].values
        s = spy_ret.loc[common].values
        cov = np.cov(t, s)[0][1]
        var = np.var(s, ddof=1)
        return round(float(cov / var), 4) if var != 0 else None
    except Exception as exc:
        logger.debug("Beta calculation error for %s: %s", symbol, exc)
        return None


def _annualized_vol_30d(history) -> float | None:
    if history is None or history.empty:
        return None
    close = history["Close"].dropna()
    if len(close) < 31:
        return None
    daily_ret = close.pct_change().dropna()
    vol = float(daily_ret.tail(30).std()) * math.sqrt(252)
    return round(vol, 6)


def _de_ratio(info: dict) -> float | None:
    de = info.get("debtToEquity")
    return float(de) / 100 if de is not None else None


# ---------------------------------------------------------------------------
# Demo entry-point  (CLAUDE.md: score_all_demo)
# ---------------------------------------------------------------------------

def score_all_demo():
    """Fetch a small set of tickers and print their risk scores."""
    import logging as _log
    _log.basicConfig(level=logging.WARNING)
    from data.fetcher import DataFetcher

    symbols = ["AAPL", "RY.TO", "XIU.TO", "ZAG.TO", "NVDA", "FTS.TO"]
    fetcher = DataFetcher()
    raw = fetcher.fetch_all(symbols)

    print(f"\n{'Symbol':<10} {'Type':<6} {'Score':>6}  {'Beta':>6}  {'Vol':>7}  {'D/E':>6}  {'P/E':>6}  {'MCap':>6}  {'Margin':>7}")
    print("-" * 75)
    for sym, d in raw.items():
        result = score_ticker(d, spy_history=fetcher.spy_history)
        c   = result["components"]
        tag = "ETF" if d["is_etf"] else "Stock"
        print(f"{sym:<10} {tag:<6} {result['risk_score']:>6.2f}  "
              f"{c['beta']:>6.1f}  {c['volatility']:>7.1f}  "
              f"{c['debt_equity']:>6.1f}  {c['pe_ratio']:>6.1f}  "
              f"{c['market_cap']:>6.1f}  {c['profit_margin']:>7.1f}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    score_all_demo()
