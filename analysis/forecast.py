"""
3-Month Forecast & Analyst Consensus module (Step 5, §9).

Public API:
    compute_forecast(ticker_data) -> dict

Two forecast models:
    Model A — analyst consensus path  (§9.1)
    Model B — dampened 3-month momentum (§9.2)

Analyst consensus aggregation (§9.4) uses upgrades_downgrades DataFrame
(yfinance's per-firm historical ratings with To Grade / From Grade columns).

Usage:
    from analysis.forecast import compute_forecast
    fc = compute_forecast(data["AAPL"])
    fc = compute_forecast(data["XIU.TO"])
"""

import logging
from datetime import datetime, timedelta, timezone

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# §9.4  Grade mapping
# ---------------------------------------------------------------------------

GRADE_MAP = {
    # buy
    "Strong Buy":    "buy",
    "Buy":           "buy",
    "Outperform":    "buy",
    "Overweight":    "buy",
    "Market Outperform": "buy",
    "Sector Outperform": "buy",
    "Positive":      "buy",
    # hold
    "Hold":          "hold",
    "Neutral":       "hold",
    "Market Perform": "hold",
    "Sector Perform": "hold",
    "Equal-Weight":  "hold",
    "In-Line":       "hold",
    "Perform":       "hold",
    # sell
    "Sell":          "sell",
    "Underperform":  "sell",
    "Underweight":   "sell",
    "Strong Sell":   "sell",
    "Market Underperform": "sell",
    "Sector Underperform": "sell",
    "Negative":      "sell",
}


def _map_grade(grade: str) -> str | None:
    if not grade or not isinstance(grade, str):
        return None
    return GRADE_MAP.get(grade.strip())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_forecast(ticker_data: dict) -> dict:
    """Compute 3-month price forecast and analyst consensus for one ticker.

    Args:
        ticker_data: Dict as returned by DataFetcher.fetch_ticker().

    Returns:
        Dict matching the §9.5 output structure.  Missing fields are None.
    """
    info    = ticker_data.get("info") or {}
    history = ticker_data.get("history")
    recs    = ticker_data.get("upgrades_downgrades")   # per-firm grade history

    current_price = _current_price(history)
    model_a       = _model_a(current_price, info)
    model_b       = _model_b(current_price, history)
    forecast_3m   = _combine(model_a, model_b)

    upside_pct = None
    if forecast_3m is not None and current_price:
        upside_pct = round((forecast_3m / current_price - 1) * 100, 2)

    consensus = _analyst_consensus(recs)

    return {
        "current_price":    round(current_price, 4) if current_price else None,
        "forecast_3m":      round(forecast_3m, 4)   if forecast_3m   else None,
        "upside_pct":       upside_pct,
        "forecast_high":    info.get("targetHighPrice"),
        "forecast_low":     info.get("targetLowPrice"),
        "model_a":          round(model_a, 4) if model_a else None,
        "model_b":          round(model_b, 4) if model_b else None,
        "analyst_count":    consensus["analyst_count"],
        "buy_count":        consensus["buy_count"],
        "hold_count":       consensus["hold_count"],
        "sell_count":       consensus["sell_count"],
        "recent_upgrades":  consensus["recent_upgrades"],
        "recent_downgrades": consensus["recent_downgrades"],
        "consensus_label":  consensus["consensus_label"],
        "recommendation_key": info.get("recommendationKey"),
    }


# ---------------------------------------------------------------------------
# §9.1  Model A — analyst consensus price path
# ---------------------------------------------------------------------------

def _model_a(current_price, info: dict):
    target = info.get("targetMeanPrice")
    if current_price is None or target is None:
        return None
    return current_price + (target - current_price) * 0.25


# ---------------------------------------------------------------------------
# §9.2  Model B — dampened momentum
# ---------------------------------------------------------------------------

def _model_b(current_price, history):
    if current_price is None or history is None or history.empty:
        return None
    close = history["Close"].dropna()
    if len(close) < 63:
        return None
    trend_3m    = (float(close.iloc[-1]) / float(close.iloc[-63])) - 1
    damped_rate = trend_3m * 0.6
    return current_price * (1 + damped_rate)


# ---------------------------------------------------------------------------
# §9.3  Combined forecast
# ---------------------------------------------------------------------------

def _combine(model_a, model_b):
    if model_a is not None and model_b is not None:
        return (model_a + model_b) / 2
    return model_a if model_a is not None else model_b


# ---------------------------------------------------------------------------
# §9.4  Analyst consensus aggregation
# ---------------------------------------------------------------------------

def _analyst_consensus(recs) -> dict:
    empty = {
        "analyst_count": 0,
        "buy_count":     0,
        "hold_count":    0,
        "sell_count":    0,
        "recent_upgrades":   0,
        "recent_downgrades": 0,
        "consensus_label":   "Insufficient Data",
    }

    if recs is None or (hasattr(recs, "empty") and recs.empty):
        return empty

    df = recs.copy()

    # Normalise index to tz-naive
    if hasattr(df.index, "tz") and df.index.tz is not None:
        df.index = df.index.tz_localize(None)

    # Identify grade columns — yfinance uses "ToGrade"/"FromGrade" (camelCase)
    # or "To Grade"/"From Grade" depending on version; normalise to find either
    col_map  = {c.lower().replace(" ", "").replace("_", ""): c for c in df.columns}
    to_col   = col_map.get("tograde")
    from_col = col_map.get("fromgrade")

    if to_col is None:
        logger.debug("upgrades_downgrades missing 'To Grade' column; skipping consensus.")
        return empty

    now      = pd.Timestamp.now()
    cut_90   = now - pd.Timedelta(days=90)
    cut_30   = now - pd.Timedelta(days=30)

    recent_90 = df[df.index >= cut_90]
    recent_30 = df[df.index >= cut_30]

    # Count buy / hold / sell in last 90 days
    buy_count = hold_count = sell_count = 0
    for grade in recent_90[to_col].dropna():
        mapped = _map_grade(str(grade))
        if mapped == "buy":
            buy_count += 1
        elif mapped == "hold":
            hold_count += 1
        elif mapped == "sell":
            sell_count += 1

    total = buy_count + hold_count + sell_count

    # Upgrades / downgrades in last 30 days
    recent_upgrades = recent_downgrades = 0
    if from_col is not None:
        for _, row in recent_30.iterrows():
            to   = _map_grade(str(row[to_col]   if to_col   in row.index else ""))
            frm  = _map_grade(str(row[from_col] if from_col in row.index else ""))
            if to == "buy" and frm in ("hold", "sell"):
                recent_upgrades += 1
            elif to in ("hold", "sell") and frm == "buy":
                recent_downgrades += 1

    # Consensus label
    if total < 3:
        label = "Insufficient Data"
    elif buy_count / total > 0.70:
        label = "Strong Buy"
    elif buy_count / total > 0.50:
        label = "Buy"
    elif hold_count / total > 0.50:
        label = "Hold"
    elif sell_count / total > 0.40:
        label = "Sell"
    else:
        label = "Mixed"

    return {
        "analyst_count":     total,
        "buy_count":         buy_count,
        "hold_count":        hold_count,
        "sell_count":        sell_count,
        "recent_upgrades":   recent_upgrades,
        "recent_downgrades": recent_downgrades,
        "consensus_label":   label,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _current_price(history) -> float | None:
    if history is None or history.empty:
        return None
    close = history["Close"].dropna()
    return float(close.iloc[-1]) if not close.empty else None


# ---------------------------------------------------------------------------
# forecast_demo  (callable from one-liner in CLAUDE.md)
# ---------------------------------------------------------------------------

def forecast_demo(symbols=None):
    """Print forecast output for a small set of tickers."""
    import logging as _log
    _log.basicConfig(level=logging.WARNING)
    from data.fetcher import DataFetcher

    if symbols is None:
        symbols = ["AAPL", "RY.TO", "XIU.TO"]
    fetcher = DataFetcher()
    raw = fetcher.fetch_all(symbols)
    for sym, d in raw.items():
        fc = compute_forecast(d)
        print(f"{sym}: forecast={fc['forecast_3m']}  upside={fc['upside_pct']}%  consensus={fc['consensus_label']}")


# ---------------------------------------------------------------------------
# CLI demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    import logging as _log
    _log.basicConfig(level=logging.WARNING)

    from data.fetcher import DataFetcher

    symbols = sys.argv[1:] if len(sys.argv) > 1 else ["AAPL", "RY.TO", "XIU.TO", "MSFT"]
    fetcher = DataFetcher()
    raw = fetcher.fetch_all(symbols)

    for sym, d in raw.items():
        fc = compute_forecast(d)
        tag = "ETF" if d["is_etf"] else "Stock"
        print(f"\n── {sym}  [{tag}] ──────────────────────────")
        print(f"  Current price    : {fc['current_price']}")
        print(f"  Model A (analyst): {fc['model_a']}")
        print(f"  Model B (momentum): {fc['model_b']}")
        print(f"  Forecast 3m      : {fc['forecast_3m']}  ({fc['upside_pct']:+.1f}%)" if fc['upside_pct'] is not None else f"  Forecast 3m      : {fc['forecast_3m']}")
        print(f"  Target high/low  : {fc['forecast_high']} / {fc['forecast_low']}")
        print(f"  Consensus        : {fc['consensus_label']}  (buy={fc['buy_count']}  hold={fc['hold_count']}  sell={fc['sell_count']}  total={fc['analyst_count']})")
        print(f"  Upgrades (30d)   : {fc['recent_upgrades']}   Downgrades: {fc['recent_downgrades']}")
        print(f"  Rec key          : {fc['recommendation_key']}")
