"""
Fundamentals module (Step 3, §7).

Public API:
    compute_fundamentals(ticker_data, spy_history=None, platform="questrade") -> dict
    compute_costs(info, is_etf, exchange, platform) -> dict

Usage:
    from analysis.fundamentals import compute_fundamentals
    fund = compute_fundamentals(data["AAPL"], spy_history=fetcher.spy_history)
    fund = compute_fundamentals(data["XIU.TO"])
"""

import logging
from datetime import datetime, timezone

import pandas as pd

from analysis.risk_scorer import _get_beta, _de_ratio
from data.ticker_selector import DISPLAY_NAMES, EXCHANGE_MAP, SECTOR_MAP

logger = logging.getLogger(__name__)


def _pct_to_dec(value):
    """yfinance ≥1.x returns dividendYield as a percentage (e.g. 2.33 for 2.33%).
    Convert to decimal so callers can use :.2% formatting directly."""
    if value is None:
        return None
    return value / 100


# ---------------------------------------------------------------------------
# §7.3  Trading cost table
# ---------------------------------------------------------------------------

TRADING_COSTS = {
    "questrade":    {"cad_stock": 7.00, "usd_stock": 7.00, "cad_etf": 0.00, "usd_etf": 7.00},
    "wealthsimple": {"cad_stock": 0.00, "usd_stock": 2.00, "cad_etf": 0.00, "usd_etf": 2.00},
    "td_direct":    {"cad_stock": 9.99, "usd_stock": 9.99, "cad_etf": 9.99, "usd_etf": 9.99},
}

_FX_SPREAD = 0.015  # 1.5% for USD-listed tickers


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_fundamentals(
    ticker_data: dict,
    spy_history=None,
    platform: str = "questrade",
    beta=None,
) -> dict:
    """Return a flat fundamentals dict for one ticker.

    Args:
        ticker_data:  Dict as returned by DataFetcher.fetch_ticker().
        spy_history:  Close Series (1yr) from DataFetcher.spy_history; used for
                      beta fallback when info['beta'] is missing.
        platform:     One of "questrade", "wealthsimple", "td_direct".
        beta:         Pre-computed beta from risk_scorer.score_ticker. If None,
                      it is derived here.

    Returns:
        Dict with all §7 fields.  Missing values are None.
    """
    symbol   = ticker_data["symbol"]
    is_etf   = ticker_data["is_etf"]
    info     = ticker_data.get("info") or {}
    history  = ticker_data.get("history")
    # Resolved from Yahoo's own listing data by the fetcher; the static map only
    # covers the 86-name pool and would call every custom symbol USD-listed.
    exchange = ticker_data.get("exchange") or EXCHANGE_MAP.get(symbol, "NYSE")

    if beta is None:
        beta = _get_beta(info, history, spy_history, symbol)

    if is_etf:
        result = _etf_fundamentals(ticker_data, info, beta, symbol)
    else:
        result = _stock_fundamentals(ticker_data, info, beta, symbol)

    result["cost"] = compute_costs(info, is_etf, exchange, platform,
                                   funds_data=ticker_data.get("funds_data"))
    return result


def compute_costs(
    info: dict,
    is_etf: bool,
    exchange: str,
    platform: str = "questrade",
    funds_data=None,
) -> dict:
    """Return the §7.3 cost sub-dict for any ticker.

    An unknown MER is reported as None, never as 0.0. Yahoo has no expense ratio
    for many TSX-listed ETFs, and the previous `or 0.0` fallback made every one of
    them look free and earned it an "Excellent" cost grade.
    """
    platform = platform if platform in TRADING_COSTS else "questrade"
    costs = TRADING_COSTS[platform]
    is_cad = exchange == "TSX"

    if is_etf:
        mer         = _etf_expense_ratio(info, funds_data)
        annual_drag = round(10_000 * mer, 2) if mer is not None else None
        efficiency  = _mer_efficiency(mer)
        trading_cad = costs["cad_etf"] if is_cad else costs["usd_etf"]
    else:
        # A stock has no MER — absent, not zero, and not a grade.
        mer         = None
        annual_drag = None
        efficiency  = None
        trading_cad = costs["cad_stock"] if is_cad else costs["usd_stock"]

    fx_applicable = not is_cad
    currency      = "CAD" if is_cad else "USD"

    return {
        "expense_ratio":          mer,
        "expense_ratio_pct":      f"{mer * 100:.2f}%" if mer is not None else None,
        "annual_drag_per_10k":    annual_drag,
        "cost_efficiency":        efficiency,
        "trading_cost":           trading_cad,
        "trading_cost_currency":  currency,
        "trading_cost_cad":       trading_cad if is_cad else None,
        "trading_cost_usd":       None if is_cad else trading_cad,
        "fx_conversion_applicable": fx_applicable,
        "fx_conversion_cost_pct": "1.50%" if fx_applicable else "0.00%",
    }


def _etf_expense_ratio(info: dict, funds_data) -> float | None:
    """MER as a decimal (0.0009 = 0.09%), or None when Yahoo doesn't have it.

    Order: funds_data.fund_operations (a decimal), then info['netExpenseRatio']
    (expressed as a *percent*, e.g. 0.0945 means 0.0945%). Note that
    fund_operations reports 0.0000 for many TSX ETFs (XIU.TO, ZAG.TO), which is
    missing data rather than a free fund — hence the `> 0` test.
    """
    try:
        if funds_data is not None:
            ops = funds_data.fund_operations
            if ops is not None and not ops.empty:
                for label in ("Annual Report Expense Ratio", "Expense Ratio"):
                    if label in ops.index:
                        for col in ops.columns:
                            v = ops.loc[label, col]
                            if v is not None and not pd.isna(v) and float(v) > 0:
                                return float(v)
                        break
    except Exception as exc:
        logger.debug("fund_operations expense ratio lookup failed: %s", exc)

    net = info.get("netExpenseRatio")
    if net is not None:
        try:
            v = float(net)
            if v > 0:
                return v / 100.0   # stored as a percent
        except (TypeError, ValueError):
            pass

    for key in ("annualReportExpenseRatio", "totalExpenseRatio"):
        v = info.get(key)
        if v is not None:
            try:
                fv = float(v)
                if fv > 0:
                    return fv
            except (TypeError, ValueError):
                pass

    return None


# ---------------------------------------------------------------------------
# §7.1  Stock fundamentals
# ---------------------------------------------------------------------------

def _stock_fundamentals(ticker_data: dict, info: dict, beta, symbol: str) -> dict:
    income_stmt   = ticker_data.get("income_stmt")
    balance_sheet = ticker_data.get("balance_sheet")
    dividends     = ticker_data.get("dividends")

    name   = info.get("longName") or DISPLAY_NAMES.get(symbol, symbol)
    sector = info.get("sector")   or SECTOR_MAP.get(symbol)

    # Prefer direct info fields (TTM, matches Yahoo Finance) with income_stmt fallback
    revenue        = info.get("totalRevenue")
    net_income     = info.get("netIncomeToCommon")
    revenue_growth = info.get("revenueGrowth")   # already a decimal (e.g. 0.166)
    if revenue is None or net_income is None:
        rev_fb, rg_fb, ni_fb = _income_fields(income_stmt)
        if revenue is None:        revenue        = rev_fb
        if net_income is None:     net_income     = ni_fb
        if revenue_growth is None: revenue_growth = rg_fb

    total_assets = _balance_sheet_field(balance_sheet, "Total Assets")
    dividend_history = _dividend_history_annual(dividends, years=5)
    annual_div_per_share = _annual_div_per_share(info, dividends)

    ex_div = info.get("exDividendDate")
    if isinstance(ex_div, (int, float)):
        try:
            ex_div = datetime.fromtimestamp(ex_div, tz=timezone.utc).strftime("%Y-%m-%d")
        except Exception:
            ex_div = None

    return {
        "name":                  name,
        "sector":                sector,
        "pe":                    info.get("trailingPE"),
        "forward_pe":            info.get("forwardPE"),
        "eps":                   info.get("trailingEps"),
        "revenue":               revenue,
        "revenue_growth":        revenue_growth,
        "net_income":            net_income,
        "net_margin":            info.get("profitMargins"),
        "total_assets":          total_assets,
        "debt_equity":           _de_ratio(info),
        "current_ratio":         info.get("currentRatio"),
        "dividend_yield":        _pct_to_dec(info.get("dividendYield")),
        "annual_div_per_share":  annual_div_per_share,
        "ex_dividend_date":      ex_div,
        "beta":                  beta,
        "market_cap":            info.get("marketCap"),
        "business_summary":      info.get("longBusinessSummary"),
        "analyst_target_mean":   info.get("targetMeanPrice"),
        "analyst_target_high":   info.get("targetHighPrice"),
        "analyst_target_low":    info.get("targetLowPrice"),
        "analyst_count":         info.get("numberOfAnalystOpinions"),
        "recommendation_key":    info.get("recommendationKey"),
        "dividend_history":      dividend_history,
    }


def _income_fields(income_stmt):
    if income_stmt is None or income_stmt.empty:
        return None, None, None

    def _first(label):
        # income_stmt columns are dates; index is the field name
        matches = [r for r in income_stmt.index if label.lower() in str(r).lower()]
        if not matches:
            return None
        row = income_stmt.loc[matches[0]]
        vals = row.dropna()
        return float(vals.iloc[0]) if not vals.empty else None

    revenue     = _first("Total Revenue")
    net_income  = _first("Net Income")

    rev_growth = None
    rev_matches = [r for r in income_stmt.index if "total revenue" in str(r).lower()]
    if rev_matches:
        row = income_stmt.loc[rev_matches[0]].dropna()
        if len(row) >= 2:
            r0, r1 = float(row.iloc[0]), float(row.iloc[1])
            if r1 != 0:
                rev_growth = (r0 - r1) / abs(r1)

    return revenue, rev_growth, net_income


def _balance_sheet_field(balance_sheet, label: str):
    if balance_sheet is None or balance_sheet.empty:
        return None
    matches = [r for r in balance_sheet.index if label.lower() in str(r).lower()]
    if not matches:
        return None
    row  = balance_sheet.loc[matches[0]].dropna()
    return float(row.iloc[0]) if not row.empty else None



def _dividend_history_annual(dividends, years: int = 5) -> dict:
    if dividends is None or dividends.empty:
        return {}
    # Normalize timezone-aware index
    idx = dividends.index
    if hasattr(idx, "tz") and idx.tz is not None:
        idx = idx.tz_localize(None)
    series = pd.Series(dividends.values, index=idx)
    cutoff = pd.Timestamp.now() - pd.DateOffset(years=years)
    recent = series[series.index >= cutoff]
    if recent.empty:
        return {}
    by_year = recent.groupby(recent.index.year).sum()
    return {int(yr): round(float(total), 4) for yr, total in by_year.items()}


def _annual_div_per_share(info: dict, dividends) -> float | None:
    last_div = info.get("lastDividendValue")
    if last_div is None:
        return None
    freq = _infer_div_frequency(dividends)
    return round(float(last_div) * freq, 4)


def _infer_div_frequency(dividends) -> int:
    """Estimate payments per year from dividend history."""
    if dividends is None or len(dividends) < 2:
        return 4  # default quarterly
    idx = dividends.index
    if hasattr(idx, "tz") and idx.tz is not None:
        idx = idx.tz_localize(None)
    recent = [d for d in idx if d >= pd.Timestamp.now() - pd.DateOffset(years=1)]
    count = len(recent)
    if count >= 10:
        return 12
    if count >= 3:
        return 4
    if count == 2:
        return 2
    return 1


# ---------------------------------------------------------------------------
# §7.2  ETF fundamentals
# ---------------------------------------------------------------------------

def _etf_fundamentals(ticker_data: dict, info: dict, beta, symbol: str) -> dict:
    funds_data = ticker_data.get("funds_data")
    dividends  = ticker_data.get("dividends")

    name        = info.get("longName") or DISPLAY_NAMES.get(symbol, symbol)
    description = info.get("longBusinessSummary")
    aum         = info.get("totalAssets")
    # Same resolution order as compute_costs, so the MER shown in the fundamentals
    # table can't disagree with the one in the cost table on the same page.
    mer         = _etf_expense_ratio(info, funds_data)
    dist_yield  = info.get("yield") or _pct_to_dec(info.get("dividendYield"))

    top_holdings  = _etf_top_holdings(funds_data)
    sector_weights = _etf_sector_weights(funds_data)
    dist_history  = _distribution_history(dividends, quarters=8)

    return {
        "name":               name,
        "description":        description,
        "aum":                float(aum) if aum is not None else None,
        "expense_ratio_raw":  mer,
        "distribution_yield": float(dist_yield) if dist_yield is not None else None,
        "beta":               beta,
        "top_holdings":       top_holdings,
        "sector_weights":     sector_weights,
        "distribution_history": dist_history,
    }


def _etf_top_holdings(funds_data) -> list:
    if funds_data is None:
        return []
    try:
        df = funds_data.top_holdings
        if df is None or (hasattr(df, "empty") and df.empty):
            return []
        records = []
        for _, row in df.head(10).iterrows():
            records.append({
                "symbol": str(row.name) if hasattr(row, "name") else str(_),
                "name":   str(row.get("holdingName", row.name) if hasattr(row, "get") else row.iloc[0]),
                "weight": float(row.get("holdingPercent", 0) if hasattr(row, "get") else 0),
            })
        return records
    except Exception as exc:
        logger.debug("top_holdings parse error: %s", exc)
        return []


def _etf_sector_weights(funds_data) -> dict:
    if funds_data is None:
        return {}
    try:
        sw = funds_data.sector_weightings
        if not sw:
            return {}
        return {str(k): float(v) for k, v in sw.items()}
    except Exception as exc:
        logger.debug("sector_weightings parse error: %s", exc)
        return {}


def _distribution_history(dividends, quarters: int = 8) -> dict:
    """Return last *quarters* quarterly distribution totals keyed by YYYY-Q#."""
    if dividends is None or dividends.empty:
        return {}
    idx = dividends.index
    if hasattr(idx, "tz") and idx.tz is not None:
        idx = idx.tz_localize(None)
    series = pd.Series(dividends.values, index=idx)
    cutoff = pd.Timestamp.now() - pd.DateOffset(months=quarters * 3)
    recent = series[series.index >= cutoff]
    if recent.empty:
        return {}
    by_quarter = recent.groupby([recent.index.year, recent.index.quarter]).sum()
    return {f"{yr}-Q{q}": round(float(v), 4) for (yr, q), v in by_quarter.items()}



def _mer_efficiency(mer: float | None) -> str | None:
    # No MER means no grade. Returning "Excellent" for missing data made every
    # TSX ETF look like the cheapest fund on the market.
    if mer is None or mer <= 0:
        return None
    if mer <= 0.0010:
        return "Excellent"
    if mer <= 0.0025:
        return "Good"
    if mer <= 0.0060:
        return "Fair"
    return "Poor"


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
        fund = compute_fundamentals(d, spy_history=fetcher.spy_history)
        print(f"\n── {sym} ({'ETF' if d['is_etf'] else 'Stock'}) ──────────────────────────")
        for k, v in fund.items():
            if k == "business_summary" and v:
                v = v[:80] + "..."
            if k == "cost":
                print(f"  {'cost':<25}  {{...}}")
                for ck, cv in v.items():
                    print(f"    {ck:<28}  {cv}")
            elif k in ("top_holdings", "dividend_history", "distribution_history"):
                print(f"  {k:<25}  {type(v).__name__}({len(v)} items)")
            else:
                print(f"  {k:<25}  {v}")
