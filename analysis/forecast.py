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
    "Outperformer":  "buy",
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
    "Sector Weight": "hold",
    "Market Weight": "hold",
    "Peer Perform":  "hold",
    # sell
    "Sell":          "sell",
    "Underperform":  "sell",
    "Underweight":   "sell",
    "Strong Sell":   "sell",
    "Market Underperform": "sell",
    "Sector Underperform": "sell",
    "Negative":      "sell",
    "Reduce":        "sell",
}

# Yahoo's recommendationKey -> the report's label vocabulary.
_RECOMMENDATION_KEY_LABEL = {
    "strong_buy":  "Strong Buy",
    "buy":         "Buy",
    "hold":        "Hold",
    "underperform": "Sell",
    "sell":        "Sell",
    "strong_sell": "Sell",
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
    trend   = ticker_data.get("recommendations")       # current analyst counts
    changes = ticker_data.get("upgrades_downgrades")   # rating-change log

    current_price = _current_price(history)
    model_a       = _model_a(current_price, info)
    model_b       = _model_b(current_price, history)
    forecast_3m   = _combine(model_a, model_b)

    upside_pct = None
    if forecast_3m is not None and current_price:
        upside_pct = round((forecast_3m / current_price - 1) * 100, 2)

    # The analyst target's own implication, on its own 12-month horizon. Kept
    # separate from the blended 3-month projection above, which is not an
    # analyst figure and for ETFs contains no analyst input at all.
    target_mean    = info.get("targetMeanPrice")
    analyst_upside = None
    if target_mean and current_price:
        analyst_upside = round((float(target_mean) / current_price - 1) * 100, 2)

    if model_a is not None and model_b is not None:
        basis = "blend"
    elif model_a is not None:
        basis = "analyst"
    elif model_b is not None:
        basis = "momentum"
    else:
        basis = None

    consensus = _analyst_consensus(trend, changes, info)

    return {
        "current_price":    round(current_price, 4) if current_price else None,
        "forecast_3m":      round(forecast_3m, 4)   if forecast_3m   else None,
        "upside_pct":       upside_pct,
        "forecast_high":    info.get("targetHighPrice"),
        "forecast_low":     info.get("targetLowPrice"),
        "model_a":          round(model_a, 4) if model_a else None,
        "model_b":          round(model_b, 4) if model_b else None,
        "forecast_basis":   basis,
        "analyst_target":   float(target_mean) if target_mean else None,
        "analyst_upside_pct": analyst_upside,
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

def _analyst_consensus(trend, changes, info: dict | None = None) -> dict:
    """Current analyst consensus, plus 30-day rating momentum.

    Counts come from Yahoo's recommendationTrend (`ticker.recommendations`), which
    is a snapshot of how many analysts currently hold each rating. The label
    prefers Yahoo's own `recommendationKey`.

    This deliberately does NOT count rows in `upgrades_downgrades`: that frame logs
    rating *changes*, so counting a 90-day window of it both missed analysts who
    hadn't changed their rating (11 of 12 TSX names reported zero coverage) and
    double-counted firms that acted twice. It is used here only for the 30-day
    upgrade/downgrade tallies, which is what a change log is actually good for.
    """
    info = info or {}

    result = {
        "analyst_count": 0,
        "buy_count":     0,
        "hold_count":    0,
        "sell_count":    0,
        "recent_upgrades":   0,
        "recent_downgrades": 0,
        "consensus_label":   "Insufficient Data",
    }

    # ── Counts from recommendationTrend ─────────────────────────────────────
    counts = _trend_counts(trend)
    if counts is not None:
        result.update(counts)

    # ── 30-day rating momentum from the change log ──────────────────────────
    result.update(_rating_momentum(changes))

    # ── Label: prefer Yahoo's own consensus key ─────────────────────────────
    key   = (info.get("recommendationKey") or "").strip().lower()
    label = _RECOMMENDATION_KEY_LABEL.get(key)

    total = result["analyst_count"]
    if label is None and total >= 3:
        # Fallback: derive from the counts we have.
        buy, hold, sell = result["buy_count"], result["hold_count"], result["sell_count"]
        if buy / total > 0.70:
            label = "Strong Buy"
        elif buy / total > 0.50:
            label = "Buy"
        elif hold / total > 0.50:
            label = "Hold"
        elif sell / total > 0.40:
            label = "Sell"
        else:
            label = "Mixed"

    if label is not None and total > 0:
        result["consensus_label"] = label

    return result


def _trend_counts(trend) -> dict | None:
    """Buy/hold/sell counts from the most recent recommendationTrend period."""
    if trend is None or (hasattr(trend, "empty") and trend.empty):
        return None
    try:
        df = trend
        cols = {c.lower(): c for c in df.columns}
        needed = ("strongbuy", "buy", "hold", "sell", "strongsell")
        if not all(c in cols for c in needed):
            logger.debug("recommendationTrend missing expected columns: %s", list(df.columns))
            return None

        # Prefer the current period ("0m"); fall back to the first available row.
        row = None
        if "period" in cols:
            match = df[df[cols["period"]].astype(str).str.strip() == "0m"]
            if not match.empty:
                row = match.iloc[0]
        if row is None:
            row = df.iloc[0]

        def _n(name):
            v = row[cols[name]]
            return 0 if pd.isna(v) else int(v)

        buy  = _n("strongbuy") + _n("buy")
        hold = _n("hold")
        sell = _n("sell") + _n("strongsell")
        total = buy + hold + sell
        if total == 0:
            return None

        return {
            "analyst_count": total,
            "buy_count":     buy,
            "hold_count":    hold,
            "sell_count":    sell,
        }
    except Exception as exc:
        logger.debug("recommendationTrend parse error: %s", exc)
        return None


def _rating_momentum(changes) -> dict:
    """Upgrades / downgrades in the last 30 days from the rating-change log."""
    out = {"recent_upgrades": 0, "recent_downgrades": 0}
    if changes is None or (hasattr(changes, "empty") and changes.empty):
        return out
    try:
        df = changes.copy()
        if hasattr(df.index, "tz") and df.index.tz is not None:
            df.index = df.index.tz_localize(None)

        # yfinance uses "ToGrade"/"FromGrade" or "To Grade"/"From Grade" by version.
        col_map  = {c.lower().replace(" ", "").replace("_", ""): c for c in df.columns}
        to_col   = col_map.get("tograde")
        from_col = col_map.get("fromgrade")
        if to_col is None or from_col is None:
            return out

        recent_30 = df[df.index >= pd.Timestamp.now() - pd.Timedelta(days=30)]
        for _, row in recent_30.iterrows():
            to  = _map_grade(str(row[to_col]))
            frm = _map_grade(str(row[from_col]))
            if to == "buy" and frm in ("hold", "sell"):
                out["recent_upgrades"] += 1
            elif to in ("hold", "sell") and frm == "buy":
                out["recent_downgrades"] += 1
        return out
    except Exception as exc:
        logger.debug("rating momentum parse error: %s", exc)
        return out


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
