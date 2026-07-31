"""
Technical analysis module (Step 3, §6).

Public API:
    compute_technicals(history) -> dict

Usage:
    from analysis.technical import compute_technicals
    techs = compute_technicals(data["AAPL"]["history"])
"""

import math

import numpy as np
import pandas as pd


def compute_technicals(history: pd.DataFrame) -> dict:
    """Compute all technical indicators for one ticker.

    Args:
        history: DataFrame with 'Close' and 'Volume' columns, DatetimeIndex.
                 As returned by yfinance Ticker.history(period='5y').

    Returns:
        Flat dict — see §6 for full field descriptions.
    """
    close = history["Close"].dropna()
    volume = history["Volume"].dropna() if "Volume" in history.columns else pd.Series(dtype=float)

    result = {}
    result.update(_moving_averages(close))
    result.update(_rsi(close))
    result.update(_returns(close))
    result.update(_trading_activity(volume))
    return result


# ---------------------------------------------------------------------------
# §6.1  Moving averages
# ---------------------------------------------------------------------------

def _moving_averages(close: pd.Series) -> dict:
    current_price = float(close.iloc[-1]) if not close.empty else None
    ma50  = float(close.rolling(50).mean().iloc[-1])  if len(close) >= 50  else None
    ma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None

    above_ma50  = (current_price > ma50)  if (current_price is not None and ma50  is not None) else None
    above_ma200 = (current_price > ma200) if (current_price is not None and ma200 is not None) else None

    return {
        "current_price": current_price,
        "ma50":          ma50,
        "ma200":         ma200,
        "above_ma50":    above_ma50,
        "above_ma200":   above_ma200,
    }


# ---------------------------------------------------------------------------
# §6.2  RSI-14
# ---------------------------------------------------------------------------

def _rsi(close: pd.Series, period: int = 14) -> dict:
    if len(close) < period + 1:
        return {"rsi": None, "rsi_signal": None}

    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    rsi_val = float((100 - (100 / (1 + rs))).iloc[-1])

    if math.isnan(rsi_val):
        return {"rsi": None, "rsi_signal": None}

    if rsi_val > 70:
        signal = "overbought"
    elif rsi_val < 30:
        signal = "oversold"
    else:
        signal = "neutral"

    return {"rsi": round(rsi_val, 2), "rsi_signal": signal}


# ---------------------------------------------------------------------------
# §6.3  Multi-period returns
# ---------------------------------------------------------------------------

# Calendar offsets rather than trading-day counts. Trading days per year vary by
# exchange and holiday calendar, so a fixed count is fragile: the old 1260-day
# lookback for 5y was unreachable from a period="5y" fetch (which returns ~1254
# bars), making return_5y None for every ticker on every report.
# Each period carries its own as-of tolerance — how far before the target date a
# bar may sit and still be accepted. One shared value doesn't work: 10 days is
# right for a 5-year lookback but would let a "1 day" return silently measure a
# week and a half.
_RETURN_PERIODS = [
    ("return_1d",  pd.DateOffset(days=1),    pd.Timedelta(days=4)),   # absorbs a long weekend
    ("return_1w",  pd.DateOffset(weeks=1),   pd.Timedelta(days=5)),
    ("return_1m",  pd.DateOffset(months=1),  pd.Timedelta(days=10)),
    ("return_3m",  pd.DateOffset(months=3),  pd.Timedelta(days=10)),
    ("return_1y",  pd.DateOffset(years=1),   pd.Timedelta(days=10)),
    ("return_3y",  pd.DateOffset(years=3),   pd.Timedelta(days=10)),
    ("return_5y",  pd.DateOffset(years=5),   pd.Timedelta(days=10)),
]


def _returns(close: pd.Series) -> dict:
    result = {key: None for key, _, _ in _RETURN_PERIODS}
    if close.empty or not isinstance(close.index, pd.DatetimeIndex):
        return result

    close = close.sort_index()
    last_date = close.index[-1]
    last      = float(close.iloc[-1])
    first     = close.index[0]

    for key, offset, tolerance in _RETURN_PERIODS:
        target = last_date - offset
        # Not enough history to cover this period at all.
        if target < first - tolerance:
            continue

        idx = close.index.asof(target)
        if pd.isna(idx):
            # Target falls just before the first bar — a period="5y" fetch starts
            # a day or two after the exact 5-year mark. Within tolerance, measure
            # from the earliest bar we have rather than reporting nothing.
            idx = first
        elif target - idx > tolerance:
            # Nearest earlier bar is far older than asked for.
            continue

        past = float(close.loc[idx])
        if past != 0:
            result[key] = round((last / past) - 1, 6)

    return result


# ---------------------------------------------------------------------------
# §6.4  Trading activity
# ---------------------------------------------------------------------------

def _trading_activity(volume: pd.Series) -> dict:
    if volume.empty:
        return {
            "vol_30d_total": None,
            "vol_30d_avg":   None,
            "vol_90d_avg":   None,
            "vol_activity":  None,
        }

    vol_30 = volume.iloc[-30:]
    vol_90 = volume.iloc[-90:]

    vol_30d_total = float(vol_30.sum())
    vol_30d_avg   = float(vol_30.mean())
    vol_90d_avg   = float(vol_90.mean())

    if vol_90d_avg == 0:
        activity = "Normal"
    elif vol_30d_avg > vol_90d_avg * 1.1:
        activity = "Above average"
    elif vol_30d_avg < vol_90d_avg * 0.9:
        activity = "Below average"
    else:
        activity = "Normal"

    return {
        "vol_30d_total": int(vol_30d_total),
        "vol_30d_avg":   int(vol_30d_avg),
        "vol_90d_avg":   int(vol_90d_avg),
        "vol_activity":  activity,
    }


# ---------------------------------------------------------------------------
# CLI demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    import logging
    logging.basicConfig(level=logging.WARNING)

    from data.fetcher import DataFetcher

    symbols = sys.argv[1:] if len(sys.argv) > 1 else ["AAPL", "RY.TO", "XIU.TO"]
    fetcher = DataFetcher()
    raw = fetcher.fetch_all(symbols)

    for sym, d in raw.items():
        t = compute_technicals(d["history"])
        print(f"\n── {sym} ──────────────────────────")
        for k, v in t.items():
            if isinstance(v, float) and not isinstance(v, bool) and abs(v) < 100:
                print(f"  {k:<18}  {v:.4f}")
            else:
                print(f"  {k:<18}  {v}")
