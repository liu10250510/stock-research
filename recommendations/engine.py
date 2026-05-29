"""
Recommendation Engine (Step 8, §11 & §13).

Wires all upstream analysis modules together to produce ranked, diversified
portfolio picks with plain-English explanations.

Public API:
    recommend(tickers, ticker_data, risk_scores, risk_metrics,
              technicals, fundamentals, forecasts, sentiments,
              macro_context, user_risk, amount=50_000, platform="questrade")
              -> dict   (§13.5 + §13.6)

    build_explanation(merged_pick) -> dict   (§11)
    engine_demo(user_risk)                   CLI demo

Usage:
    from recommendations.engine import recommend, build_explanation
    result = recommend(tickers, ..., user_risk=5)

    python -c "from recommendations.engine import engine_demo; engine_demo(3); engine_demo(8)"
"""

import logging
from collections import defaultdict

import pandas as pd

from data.ticker_selector import SECTOR_MAP, EXCHANGE_MAP, ETF_TICKERS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_RISK_BANDS: dict[tuple, tuple] = {
    (1, 3):  (1.0, 4.5),
    (4, 6):  (3.0, 7.0),
    (7, 10): (5.5, 10.0),
}

_TARGET_PICKS: dict[tuple, int] = {
    (1, 3):  8,
    (4, 6):  10,
    (7, 10): 10,
}

CONSENSUS_RATING: dict[str, int] = {
    "Strong Buy":        5,
    "Buy":               4,
    "Hold":              3,
    "Mixed":             3,
    "Sell":              2,
    "Insufficient Data": 3,
}

_CORR_THRESHOLD  = 0.70
_MAX_CORR_PASSES = 3
_ETF_MAX         = 3   # total ETF picks across all ETF categories
_SECTOR_STOCK_MAX = 2  # picks per non-ETF sector

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def recommend(
    tickers: list,
    ticker_data: dict,
    risk_scores: dict,
    risk_metrics: dict,
    technicals: dict,
    fundamentals: dict,
    forecasts: dict,
    sentiments: dict,
    macro_context: dict,
    user_risk: int,
    amount: int = 50_000,
    platform: str = "questrade",
) -> dict:
    """Run the full recommendation pipeline; return §13.5 + §13.6 output dict.

    Never raises. Returns empty picks if no tickers pass risk band filtering.
    """
    # §13.1 — Risk band filter
    eligible = _filter_by_risk_band(tickers, risk_scores, user_risk)
    if not eligible:
        logger.warning("No tickers in risk band for user_risk=%d; widening ±1.", user_risk)
        eligible = _filter_by_risk_band(tickers, risk_scores, user_risk, widen=1)
    if not eligible:
        logger.warning("Still no eligible tickers after widening — returning empty picks.")
        return _empty_output(macro_context)

    # §13.2 — Quality scores
    scores = _compute_quality_scores(
        eligible, risk_metrics, technicals, fundamentals, forecasts, macro_context
    )

    # Rank by macro-adjusted quality descending
    ranked = sorted(eligible, key=lambda s: scores[s]["macro_adjusted_quality"], reverse=True)

    # §13.3-A — Sector caps (greedy)
    selected = _apply_sector_caps(ranked)

    # §13.3-B — Correlation filter
    selected = _apply_correlation_filter(selected, ranked, ticker_data, scores)

    # §13.3-C — Currency balance
    selected = _apply_currency_balance(selected, ranked, user_risk, scores)

    # §13.4 — Portfolio weights
    weights = _compute_weights(selected, scores)

    # §13.5 — Assemble merged pick dicts with explanations
    picks = []
    for sym in selected:
        pick = _assemble_pick(
            sym, weights[sym], scores[sym],
            risk_scores, risk_metrics, technicals, fundamentals, forecasts, sentiments,
        )
        pick["explanation"] = build_explanation(pick)
        picks.append(pick)

    # §13.6 — Portfolio cost summary
    portfolio_cost = _compute_portfolio_cost(picks, amount, platform)

    # Aggregate breakdown stats
    sector_breakdown: dict[str, int] = defaultdict(int)
    currency_breakdown = {"CAD": 0, "USD": 0}
    for sym in selected:
        sector_breakdown[SECTOR_MAP.get(sym, "Unknown")] += 1
        if EXCHANGE_MAP.get(sym, "NYSE") == "TSX":
            currency_breakdown["CAD"] += 1
        else:
            currency_breakdown["USD"] += 1

    return {
        "picks":               picks,
        "sector_breakdown":    dict(sector_breakdown),
        "currency_breakdown":  currency_breakdown,
        "avg_portfolio_corr":  _avg_portfolio_corr(selected, ticker_data),
        "total_picks":         len(picks),
        "macro_context":       macro_context,
        "portfolio_cost":      portfolio_cost,
    }


def build_explanation(merged_pick: dict) -> dict:
    """Generate §11 "Why this pick" bullets and "Watch out for" warnings.

    Args:
        merged_pick: Assembled pick dict as returned by _assemble_pick().

    Returns:
        {"why_bullets": list[str], "watch_out": list[str]}
    """
    f          = merged_pick.get("fundamentals", {}) or {}
    t          = merged_pick.get("technicals",   {}) or {}
    fc         = merged_pick.get("forecast",     {}) or {}
    s          = merged_pick.get("sentiment",    {}) or {}
    cost       = f.get("cost", {}) or {}
    is_etf     = merged_pick.get("is_etf", False)

    sharpe     = merged_pick.get("sharpe_ratio")
    max_dd     = merged_pick.get("max_drawdown")
    ann_vol    = merged_pick.get("annualized_vol")

    beta       = f.get("beta")
    margin     = f.get("net_margin")
    rev_growth = f.get("revenue_growth")
    div_yield  = f.get("dividend_yield")
    de         = f.get("debt_equity")
    pe         = f.get("pe")
    mer        = cost.get("expense_ratio", 0.0) or 0.0

    rsi        = t.get("rsi")
    return_3m  = t.get("return_3m")
    return_1y  = t.get("return_1y")

    upside_pct     = fc.get("upside_pct")
    buy_count      = fc.get("buy_count",      0) or 0
    hold_count     = fc.get("hold_count",     0) or 0
    sell_count     = fc.get("sell_count",     0) or 0
    total_count    = buy_count + hold_count + sell_count
    upgrades       = fc.get("recent_upgrades", 0) or 0
    consensus      = fc.get("consensus_label", "")

    risk_factors   = s.get("risk_factors",    []) or []
    sent_label     = s.get("sentiment_label", "Neutral")

    # ── "Why this pick" bullets (§11.1) ─────────────────────────────────────

    bullets: list[str] = []

    if sharpe is not None and sharpe > 1.0:
        ret_str = f"{return_1y:.1%}" if return_1y is not None else "positive"
        bullets.append(
            f"Strong risk-adjusted return: Sharpe ratio of {sharpe:.2f} — "
            f"earns {ret_str} return per unit of risk."
        )

    if beta is not None and beta < 0.8:
        bullets.append(
            f"Low market sensitivity: beta of {beta:.2f} — moves less than the broad market."
        )

    if margin is not None and margin > 0.15:
        bullets.append(
            f"High profitability: {margin:.1%} net margin, "
            f"above the 15% threshold for quality businesses."
        )

    if rev_growth is not None and rev_growth > 0.10:
        bullets.append(f"Growing business: revenue up {rev_growth:.1%} year-over-year.")

    if div_yield is not None and div_yield > 0.02:
        bullets.append(f"Income component: {div_yield:.2%} dividend yield.")

    if consensus in ("Strong Buy", "Buy") and total_count >= 3:
        bullets.append(
            f"Analyst conviction: {buy_count} of {total_count} analysts "
            f"rate it Buy or Strong Buy."
        )

    if upgrades > 0:
        bullets.append(
            f"{upgrades} analyst upgrade(s) in the last 30 days — recent positive re-rating."
        )

    if upside_pct is not None and upside_pct > 10:
        bullets.append(
            f"Analyst price target implies {upside_pct:.1f}% upside from current price over 12 months."
        )

    if rsi is not None and rsi < 45:
        bullets.append(
            f"Technical entry: RSI of {rsi:.0f} — not overbought, reasonable entry point."
        )

    if return_3m is not None and return_3m > 0.05:
        bullets.append(f"Positive momentum: up {return_3m:.1%} over the last 3 months.")

    if de is not None and de < 0.5:
        bullets.append(
            f"Clean balance sheet: debt/equity of {de:.2f} — low financial leverage."
        )

    if is_etf and mer > 0 and mer < 0.0015:
        bullets.append(
            f"Cost-efficient ETF: expense ratio of {mer:.2%} — low drag on long-term returns."
        )

    # ── "Watch out for" warnings (§11.2) ────────────────────────────────────

    warnings: list[str] = []

    if pe is not None and pe > 40:
        warnings.append(
            f"High valuation: P/E of {pe:.1f} — priced for continued strong growth; "
            f"leaves little room for disappointment."
        )

    if max_dd is not None and max_dd < -0.40:
        warnings.append(
            f"Significant past drawdown: fell {max_dd:.1%} at its worst over 5 years — "
            f"expect volatility."
        )

    if sharpe is not None and sharpe < 0.5:
        warnings.append(
            f"Weak risk-adjusted return: Sharpe of {sharpe:.2f} — "
            f"returns haven't well-compensated for the risk taken."
        )

    if total_count >= 3:
        sell_pct = sell_count / total_count
        if sell_pct > 0.20:
            warnings.append(
                f"{sell_pct:.0%} of analysts rate it Sell — notable minority bearish view."
            )

    if sent_label == "Negative":
        warnings.append(
            "External sentiment is negative: analysts and media coverage skew cautious."
        )

    if risk_factors:
        cats = ", ".join({rf["category"] for rf in risk_factors})
        warnings.append(f"Identified risks: {cats} — review before investing.")

    if ann_vol is not None and ann_vol > 0.30:
        warnings.append(
            f"High volatility: {ann_vol:.1%} annualized — "
            f"price can swing significantly in short periods."
        )

    return {
        "why_bullets": bullets[:5],
        "watch_out":   warnings[:3],
    }


# ---------------------------------------------------------------------------
# §13.1 — Risk band filter
# ---------------------------------------------------------------------------

def _filter_by_risk_band(
    tickers: list, risk_scores: dict, user_risk: int, widen: int = 0
) -> list:
    lo, hi = _band_for_risk(user_risk)
    lo = max(1.0, lo - widen)
    hi = min(10.0, hi + widen)
    return [
        s for s in tickers
        if risk_scores.get(s, {}).get("risk_score") is not None
        and lo <= risk_scores[s]["risk_score"] <= hi
    ]


def _band_for_risk(user_risk: int) -> tuple:
    for (lo_r, hi_r), band in _RISK_BANDS.items():
        if lo_r <= user_risk <= hi_r:
            return band
    return (3.0, 7.0)


# ---------------------------------------------------------------------------
# §13.2 — Quality scores
# ---------------------------------------------------------------------------

def _compute_quality_scores(
    eligible: list,
    risk_metrics: dict,
    technicals: dict,
    fundamentals: dict,
    forecasts: dict,
    macro_context: dict,
) -> dict:
    """Return {sym: {"quality_score": float, "macro_adjusted_quality": float}}."""
    sector_mods = macro_context.get("sector_modifiers", {})
    result: dict[str, dict] = {}

    for sym in eligible:
        rm = risk_metrics.get(sym,   {}) or {}
        t  = technicals.get(sym,     {}) or {}
        f  = fundamentals.get(sym,   {}) or {}
        fc = forecasts.get(sym,      {}) or {}

        sharpe      = rm.get("sharpe_ratio") or 0.0
        analyst_r   = CONSENSUS_RATING.get(fc.get("consensus_label", ""), 3)
        momentum_1m = t.get("return_1m") or 0.0

        if sym in ETF_TICKERS:
            quality = sharpe * 0.35 + analyst_r * 0.30 + momentum_1m * 0.35
        else:
            margin   = f.get("net_margin") or 0.0
            low_debt = _debt_to_low_debt_score(f.get("debt_equity"))
            quality  = (
                sharpe      * 0.30
                + analyst_r * 0.25
                + momentum_1m * 0.20
                + margin    * 0.15
                + low_debt  * 0.10
            )

        modifier = sector_mods.get(SECTOR_MAP.get(sym, ""), 0.0)
        macro_q  = max(0.5, min(5.0, quality + modifier))

        result[sym] = {
            "quality_score":          round(quality, 4),
            "macro_adjusted_quality": round(macro_q, 4),
        }

    return result


def _debt_to_low_debt_score(de) -> float:
    """Convert D/E ratio to a 0–5 stability score (higher = lower debt = better)."""
    if de is None:
        return 1.0
    if de <= 0.3:
        return 5.0
    if de <= 0.7:
        return 4.0
    if de <= 1.5:
        return 3.0
    if de <= 3.0:
        return 2.0
    return 1.0


# ---------------------------------------------------------------------------
# §13.3-A — Sector caps
# ---------------------------------------------------------------------------

def _apply_sector_caps(ranked: list) -> list:
    """Greedy selection respecting per-sector and ETF-total caps."""
    sector_counts: dict[str, int] = defaultdict(int)
    etf_count = 0
    selected: list[str] = []

    for sym in ranked:
        if sym in ETF_TICKERS:
            if etf_count < _ETF_MAX:
                selected.append(sym)
                etf_count += 1
        else:
            sector = SECTOR_MAP.get(sym, "Unknown")
            if sector_counts[sector] < _SECTOR_STOCK_MAX:
                selected.append(sym)
                sector_counts[sector] += 1

    return selected


# ---------------------------------------------------------------------------
# §13.3-B — Correlation filter
# ---------------------------------------------------------------------------

def _apply_correlation_filter(
    selected: list,
    ranked: list,
    ticker_data: dict,
    scores: dict,
) -> list:
    """Iteratively replace high-correlation pairs (> 0.70) with alternatives."""
    if len(selected) < 2:
        return selected

    for _ in range(_MAX_CORR_PASSES):
        try:
            returns = {
                s: ticker_data[s]["history"]["Close"].pct_change().tail(252)
                for s in selected
                if s in ticker_data and not ticker_data[s]["history"].empty
            }
            if len(returns) < 2:
                break

            corr = pd.DataFrame(returns).corr()
            if corr.isnull().all().all():
                break

            # Find worst offending pair above threshold
            worst_pair = None
            worst_val  = _CORR_THRESHOLD
            for i, a in enumerate(selected):
                for b in selected[i + 1:]:
                    if a not in corr.index or b not in corr.columns:
                        continue
                    val = corr.at[a, b]
                    if not pd.isna(val) and val > worst_val:
                        worst_val  = val
                        worst_pair = (a, b)

            if worst_pair is None:
                break  # all pairs within threshold

            a, b = worst_pair
            to_remove = (
                b if scores[a]["macro_adjusted_quality"] >= scores[b]["macro_adjusted_quality"]
                else a
            )

            # Find next-best eligible candidate not already selected
            selected_set = set(selected)
            replacement  = next(
                (c for c in ranked if c not in selected_set and c != to_remove),
                None,
            )

            selected = [s for s in selected if s != to_remove]
            if replacement:
                selected.append(replacement)
                selected = [s for s in ranked if s in set(selected)]  # restore rank order

        except Exception as exc:
            logger.warning("Correlation filter pass failed: %s — skipping.", exc)
            break

    return selected


# ---------------------------------------------------------------------------
# §13.3-C — Currency balance
# ---------------------------------------------------------------------------

def _apply_currency_balance(
    selected: list,
    ranked: list,
    user_risk: int,
    scores: dict,
) -> list:
    """Ensure a minimum number of TSX-listed (CAD) picks per risk band."""
    if user_risk >= 7:
        return selected  # no minimum for aggressive

    min_cad = 3 if user_risk <= 3 else 2

    def _is_cad(sym: str) -> bool:
        return EXCHANGE_MAP.get(sym, "NYSE") == "TSX"

    cad_count    = sum(1 for s in selected if _is_cad(s))
    selected_set = set(selected)

    for candidate in ranked:
        if cad_count >= min_cad:
            break
        if candidate in selected_set or not _is_cad(candidate):
            continue

        # Swap out the lowest-quality USD pick
        usd_picks = [s for s in selected if not _is_cad(s)]
        if not usd_picks:
            break

        worst_usd = min(usd_picks, key=lambda s: scores[s]["macro_adjusted_quality"])
        selected  = [s for s in selected if s != worst_usd]
        selected.append(candidate)
        selected_set = set(selected)
        selected     = [s for s in ranked if s in selected_set]  # restore rank order
        cad_count   += 1

    return selected


# ---------------------------------------------------------------------------
# §13.4 — Portfolio weights
# ---------------------------------------------------------------------------

def _compute_weights(selected: list, scores: dict) -> dict:
    """Return {sym: weight_pct} as int multiples of 5, summing to 100."""
    n = len(selected)
    if n == 0:
        return {}

    qualities = [scores[s]["macro_adjusted_quality"] for s in selected]
    mean_q    = sum(qualities) / n or 1.0

    raw   = {s: (1.0 / n) * (scores[s]["macro_adjusted_quality"] / mean_q) for s in selected}
    total = sum(raw.values())
    pcts  = {s: raw[s] / total * 100 for s in selected}

    rounded = {s: round(pcts[s] / 5) * 5 for s in selected}

    # Absorb rounding error into largest position
    diff    = 100 - sum(rounded.values())
    if diff != 0:
        largest = max(rounded, key=lambda s: rounded[s])
        rounded[largest] += diff

    return rounded


# ---------------------------------------------------------------------------
# §13.5 — Assemble pick dict
# ---------------------------------------------------------------------------

def _assemble_pick(
    sym: str,
    weight: int,
    score_info: dict,
    risk_scores: dict,
    risk_metrics: dict,
    technicals: dict,
    fundamentals: dict,
    forecasts: dict,
    sentiments: dict,
) -> dict:
    rs = risk_scores.get(sym,   {}) or {}
    rm = risk_metrics.get(sym,  {}) or {}
    t  = technicals.get(sym,    {}) or {}
    f  = fundamentals.get(sym,  {}) or {}
    fc = forecasts.get(sym,     {}) or {}
    s  = sentiments.get(sym,    {}) or {}

    consensus = {
        "buy_count":          fc.get("buy_count",       0),
        "hold_count":         fc.get("hold_count",      0),
        "sell_count":         fc.get("sell_count",      0),
        "recent_upgrades":    fc.get("recent_upgrades", 0),
        "recent_downgrades":  fc.get("recent_downgrades", 0),
        "consensus_label":    fc.get("consensus_label", "Insufficient Data"),
        "recommendation_key": fc.get("recommendation_key"),
        "analyst_count":      fc.get("analyst_count",   0),
    }

    return {
        "symbol":                  sym,
        "is_etf":                  sym in ETF_TICKERS,
        "risk_score":              rs.get("risk_score"),
        "quality_score":           score_info["quality_score"],
        "macro_adjusted_quality":  score_info["macro_adjusted_quality"],
        "weight":                  weight,
        "sharpe_ratio":            rm.get("sharpe_ratio"),
        "sortino_ratio":           rm.get("sortino_ratio"),
        "max_drawdown":            rm.get("max_drawdown"),
        "annualized_vol":          rm.get("annualized_vol"),
        "forecast":                fc,
        "fundamentals":            f,
        "technicals":              t,
        "consensus":               consensus,
        "sentiment":               s,
        "cost":                    f.get("cost", {}),
    }


# ---------------------------------------------------------------------------
# §13.6 — Portfolio cost summary
# ---------------------------------------------------------------------------

def _compute_portfolio_cost(picks: list, amount: int, platform: str) -> dict:
    total_trading  = 0.0
    total_fx       = 0.0
    total_etf_drag = 0.0
    etf_breakdown: list[dict] = []

    for pick in picks:
        w    = pick.get("weight", 0)
        cost = pick.get("cost", {}) or {}
        pos  = amount * (w / 100)

        exp_ratio = cost.get("expense_ratio", 0.0) or 0.0
        trading   = cost.get("trading_cost_cad", 0.0) or 0.0
        fx_applic = cost.get("fx_conversion_applicable", False)

        drag    = pos * exp_ratio
        fx_cost = pos * 0.015 if fx_applic else 0.0

        total_trading  += trading
        total_fx       += fx_cost
        total_etf_drag += drag

        if exp_ratio > 0:
            etf_breakdown.append({
                "symbol":                 pick["symbol"],
                "mer_pct":               f"{exp_ratio * 100:.2f}%",
                "annual_drag_on_position": round(drag, 2),
            })

    avg_mer = total_etf_drag / amount if amount > 0 else 0.0
    if avg_mer <= 0.001:
        eff = "Excellent"
    elif avg_mer <= 0.0025:
        eff = "Good"
    elif avg_mer <= 0.006:
        eff = "Fair"
    else:
        eff = "Poor"

    _platform_notes = {
        "questrade":    "ETF buys are free on Questrade. USD positions incur ~1.5% FX conversion.",
        "wealthsimple": "Commission-free on Wealthsimple. USD positions incur ~1.5% FX conversion.",
        "td_direct":    "All trades $9.99 on TD Direct. USD positions incur ~1.5% FX conversion.",
    }

    return {
        "assumed_portfolio_cad":       amount,
        "total_one_time_trading_cost": round(total_trading, 2),
        "total_fx_conversion_cost":    round(total_fx, 2),
        "total_upfront_cost":          round(total_trading + total_fx, 2),
        "annual_etf_drag_cad":         round(total_etf_drag, 2),
        "annual_etf_drag_pct":         round(avg_mer * 100, 4),
        "etf_cost_efficiency":         eff,
        "cost_efficiency_breakdown":   etf_breakdown,
        "platform":                    platform,
        "note":                        _platform_notes.get(platform, ""),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _avg_portfolio_corr(selected: list, ticker_data: dict) -> float | None:
    """Average pairwise Pearson correlation of 1yr returns across selected picks."""
    try:
        returns = {
            s: ticker_data[s]["history"]["Close"].pct_change().tail(252)
            for s in selected
            if s in ticker_data and not ticker_data[s]["history"].empty
        }
        if len(returns) < 2:
            return None
        corr = pd.DataFrame(returns).corr()
        syms = list(returns.keys())
        vals = [
            corr.at[a, b]
            for i, a in enumerate(syms)
            for b in syms[i + 1:]
            if not pd.isna(corr.at[a, b])
        ]
        return round(sum(vals) / len(vals), 4) if vals else None
    except Exception:
        return None


def _empty_output(macro_context: dict) -> dict:
    return {
        "picks":               [],
        "sector_breakdown":    {},
        "currency_breakdown":  {"CAD": 0, "USD": 0},
        "avg_portfolio_corr":  None,
        "total_picks":         0,
        "macro_context":       macro_context,
        "portfolio_cost":      {},
    }


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

def engine_demo(user_risk: int = 5) -> None:
    """Fetch live data for a mixed ticker set and print recommendation output."""
    import logging as _log
    _log.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    from data.fetcher import DataFetcher
    from analysis.risk_scorer import score_ticker
    from analysis.risk_metrics import compute_risk_metrics
    from analysis.technical import compute_technicals
    from analysis.fundamentals import compute_fundamentals
    from analysis.forecast import compute_forecast
    from analysis.macro import fetch_macro_context

    DEMO_TICKERS = [
        "AAPL", "MSFT", "GOOGL", "NVDA",
        "RY.TO", "TD.TO", "JPM",
        "ENB.TO", "XOM",
        "XIU.TO", "ZAG.TO", "SPY",
        "WMT", "JNJ",
    ]

    print(f"\n{'='*60}")
    print(f" Recommendation Engine Demo  —  user_risk={user_risk}")
    print(f"{'='*60}")
    print("Fetching data …")

    fetcher   = DataFetcher()
    spy_hist  = fetcher.spy_history
    raw       = fetcher.fetch_all(DEMO_TICKERS)

    risk_scores_d: dict  = {}
    risk_metrics_d: dict = {}
    technicals_d: dict   = {}
    fundamentals_d: dict = {}
    forecasts_d: dict    = {}
    sentiments_d: dict   = {}

    for sym, data in raw.items():
        hist = data.get("history")
        if hist is None or hist.empty:
            continue
        risk_scores_d[sym]   = score_ticker(data, spy_hist)
        risk_metrics_d[sym]  = compute_risk_metrics(data)
        technicals_d[sym]    = compute_technicals(hist)
        fundamentals_d[sym]  = compute_fundamentals(data, spy_hist)
        forecasts_d[sym]     = compute_forecast(data)
        sentiments_d[sym]    = {
            "raw_summary": None, "sentiment_score": 0.0,
            "sentiment_label": "Neutral", "risk_factors": [],
            "sources": [], "source_scores": [],
        }

    print("Fetching macro context …")
    macro_ctx = fetch_macro_context()
    print(f"Regime: {macro_ctx['regime_description']}\n")

    result = recommend(
        tickers=list(raw.keys()),
        ticker_data=raw,
        risk_scores=risk_scores_d,
        risk_metrics=risk_metrics_d,
        technicals=technicals_d,
        fundamentals=fundamentals_d,
        forecasts=forecasts_d,
        sentiments=sentiments_d,
        macro_context=macro_ctx,
        user_risk=user_risk,
    )

    picks = result["picks"]
    print(f"Selected {len(picks)} picks:\n")
    print(f"  {'Symbol':<10} {'Risk':>5} {'Quality':>8} {'MacroQ':>8} {'Wt%':>4}  Consensus")
    print(f"  {'-'*10} {'-'*5} {'-'*8} {'-'*8} {'-'*4}  ---------")
    for p in picks:
        print(
            f"  {p['symbol']:<10} {(p['risk_score'] or 0):>5.2f} "
            f"{p['quality_score']:>8.4f} {p['macro_adjusted_quality']:>8.4f} "
            f"{p['weight']:>3}%  {p['consensus']['consensus_label']}"
        )

    print(f"\nSector breakdown : {result['sector_breakdown']}")
    print(f"Currency         : {result['currency_breakdown']}")
    avg_c = result.get("avg_portfolio_corr")
    print(f"Avg correlation  : {avg_c:.3f}" if avg_c is not None else "Avg correlation  : n/a")

    print("\nExplanations for top 3 picks:")
    for p in picks[:3]:
        print(f"\n  ── {p['symbol']} ──")
        for b in p["explanation"]["why_bullets"]:
            print(f"    + {b}")
        for w in p["explanation"]["watch_out"]:
            print(f"    ! {w}")

    cost = result["portfolio_cost"]
    print(f"\nPortfolio cost (${cost.get('assumed_portfolio_cad', 0):,} CAD):")
    print(f"  One-time trading + FX : ${cost.get('total_upfront_cost', 0):.2f}")
    print(f"  Annual ETF drag        : ${cost.get('annual_etf_drag_cad', 0):.2f} "
          f"({cost.get('annual_etf_drag_pct', 0):.3f}%)")
    print(f"  Note: {cost.get('note', '')}")
