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

import numpy as np
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

def _is_etf(sym: str, ticker_data: dict | None) -> bool:
    """ETF-ness from the fetched listing data, falling back to the static pool.

    ETF_TICKERS only covers the 86-name pool, so custom symbols outside it (e.g.
    VFV.TO) were previously analysed as individual stocks.
    """
    d = (ticker_data or {}).get(sym)
    if d is not None and "is_etf" in d:
        return bool(d["is_etf"])
    return sym in ETF_TICKERS


def _is_cad(sym: str, ticker_data: dict | None) -> bool:
    """TSX-listed (CAD) from the fetched listing data, falling back to the map."""
    d = (ticker_data or {}).get(sym)
    if d is not None and d.get("exchange"):
        return d["exchange"] == "TSX"
    return EXCHANGE_MAP.get(sym, "NYSE") == "TSX"


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
    custom_mode: bool = False,
) -> dict:
    """Run the full recommendation pipeline; return §13.5 + §13.6 output dict.

    Never raises. Returns empty picks if no tickers pass risk band filtering.

    When custom_mode=True (user-supplied symbol list), diversification caps and
    correlation/currency filters are skipped so that all supplied tickers that
    can be scored are returned, ranked by quality.
    """
    # §13.1 — Risk band filter (relaxed to full range in custom mode)
    if custom_mode:
        eligible = [t for t in tickers if risk_scores.get(t, {}).get("risk_score") is not None]
        if not eligible:
            logger.warning("No tickers could be scored — returning empty picks.")
            return _empty_output(macro_context)
    else:
        eligible = _filter_by_risk_band(tickers, risk_scores, user_risk)
        if not eligible:
            logger.warning("No tickers in risk band for user_risk=%d; widening ±1.", user_risk)
            eligible = _filter_by_risk_band(tickers, risk_scores, user_risk, widen=1)
        if not eligible:
            logger.warning("Still no eligible tickers after widening — returning empty picks.")
            return _empty_output(macro_context)

    # Per-symbol daily returns are needed by the correlation filter and again for
    # the final matrix. Memoised here so pct_change runs at most once per symbol.
    _returns_memo: dict = {}

    # §13.2 — Quality scores
    scores = _compute_quality_scores(
        eligible, risk_metrics, technicals, fundamentals, forecasts, macro_context,
        ticker_data,
    )

    # Rank by macro-adjusted quality descending
    ranked = sorted(eligible, key=lambda s: scores[s]["macro_adjusted_quality"], reverse=True)

    if custom_mode:
        # In custom mode keep every scored ticker — user chose these explicitly
        selected = ranked
    else:
        # §13.3-A — Sector caps (greedy)
        selected = _apply_sector_caps(ranked, ticker_data)

        # §13.3-B — Correlation filter
        selected = _apply_correlation_filter(selected, ranked, ticker_data, scores,
                                             memo=_returns_memo)

        # §13.3-C — Currency balance
        selected = _apply_currency_balance(selected, ranked, user_risk, scores, ticker_data)

    # §13.4 — Portfolio weights
    weights = _compute_weights(selected, scores)

    # §13.5 — Assemble merged pick dicts with explanations
    picks = []
    for sym in selected:
        pick = _assemble_pick(
            sym, weights[sym], scores[sym],
            risk_scores, risk_metrics, technicals, fundamentals, forecasts, sentiments,
            is_etf=_is_etf(sym, ticker_data),
            is_cad=_is_cad(sym, ticker_data),
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
        if _is_cad(sym, ticker_data):
            currency_breakdown["CAD"] += 1
        else:
            currency_breakdown["USD"] += 1

    # Built once for the final selection and published so the PDF's correlation
    # heatmap and the average below both reuse it instead of recomputing.
    corr_matrix = _corr_matrix(selected, ticker_data, _returns_memo)

    return {
        "picks":               picks,
        "sector_breakdown":    dict(sector_breakdown),
        "currency_breakdown":  currency_breakdown,
        "avg_portfolio_corr":  _avg_portfolio_corr(corr_matrix),
        "correlation_matrix":  corr_matrix,
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

    # upside_pct comes from a blended analyst+momentum model over 3 months, so it
    # must not be attributed to analysts. When the ticker genuinely has analyst
    # targets, quote the analyst implication separately and on its own horizon.
    if upside_pct is not None and upside_pct > 10:
        basis = "analyst targets and price momentum" if fc.get("model_a") else "price momentum"
        bullets.append(
            f"3-month projection implies {upside_pct:.1f}% upside, based on {basis}."
        )

    analyst_upside = fc.get("analyst_upside_pct")
    if analyst_upside is not None and analyst_upside > 10:
        bullets.append(
            f"Analyst price target implies {analyst_upside:.1f}% upside over 12 months."
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

def _norm(value: float, lo: float, hi: float) -> float:
    """Clamp *value* into [lo, hi] and rescale to 0-1."""
    if hi == lo:
        return 0.0
    return max(0.0, min(1.0, (float(value) - lo) / (hi - lo)))


def _compute_quality_scores(
    eligible: list,
    risk_metrics: dict,
    technicals: dict,
    fundamentals: dict,
    forecasts: dict,
    macro_context: dict,
    ticker_data: dict | None = None,
) -> dict:
    """Return {sym: {"quality_score": float, "macro_adjusted_quality": float}}.

    Every input is normalised to 0–1 before weighting. Previously the terms were
    on wildly different scales — analyst rating and low-debt on 1–5, Sharpe on
    ~0–3.5, but momentum and margin as raw decimals (0.17, 0.25) — so the nominal
    20% momentum weight contributed about 1% of the score and the 15% margin
    weight about 4%. The stated weights now actually bind.
    """
    sector_mods = macro_context.get("sector_modifiers", {})
    result: dict[str, dict] = {}

    for sym in eligible:
        rm = risk_metrics.get(sym,   {}) or {}
        t  = technicals.get(sym,     {}) or {}
        f  = fundamentals.get(sym,   {}) or {}
        fc = forecasts.get(sym,      {}) or {}

        # Sharpe: -1..3 is the meaningful range for a 1-year window.
        sharpe_n    = _norm(rm.get("sharpe_ratio") or 0.0, -1.0, 3.0)
        # Consensus rating is 1..5.
        analyst_n   = _norm(CONSENSUS_RATING.get(fc.get("consensus_label", ""), 3), 1.0, 5.0)
        # A month of +/-15% covers all but extreme moves.
        momentum_n  = _norm(t.get("return_1m") or 0.0, -0.15, 0.15)

        if _is_etf(sym, ticker_data):
            quality = sharpe_n * 0.35 + analyst_n * 0.30 + momentum_n * 0.35
        else:
            # Net margin: 0-40% spans loss-making to highly profitable.
            margin_n   = _norm(f.get("net_margin") or 0.0, 0.0, 0.40)
            # _debt_to_low_debt_score returns 1..5.
            low_debt_n = _norm(_debt_to_low_debt_score(f.get("debt_equity")), 1.0, 5.0)
            quality = (
                sharpe_n     * 0.30
                + analyst_n  * 0.25
                + momentum_n * 0.20
                + margin_n   * 0.15
                + low_debt_n * 0.10
            )

        # Rescale the 0-1 composite onto the 0.5-5.0 band the macro modifier and
        # downstream clamping already assume.
        quality *= 5.0

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

def _apply_sector_caps(ranked: list, ticker_data: dict | None = None) -> list:
    """Greedy selection respecting per-sector and ETF-total caps."""
    sector_counts: dict[str, int] = defaultdict(int)
    etf_count = 0
    selected: list[str] = []

    for sym in ranked:
        if _is_etf(sym, ticker_data):
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

def _daily_returns(symbol: str, ticker_data: dict, memo: dict):
    """Trailing-1yr daily returns for *symbol*, computed at most once per run."""
    if symbol not in memo:
        d = ticker_data.get(symbol)
        hist = d.get("history") if d else None
        if hist is None or hist.empty:
            memo[symbol] = None
        else:
            memo[symbol] = hist["Close"].pct_change().tail(252)
    return memo[symbol]


def _corr_matrix(symbols: list, ticker_data: dict, memo: dict):
    """Correlation matrix over *symbols*, or None if fewer than 2 are usable."""
    returns = {}
    for s in symbols:
        r = _daily_returns(s, ticker_data, memo)
        if r is not None:
            returns[s] = r
    if len(returns) < 2:
        return None
    return pd.DataFrame(returns).corr()


def _apply_correlation_filter(
    selected: list,
    ranked: list,
    ticker_data: dict,
    scores: dict,
    memo: dict | None = None,
) -> list:
    """Iteratively replace high-correlation pairs (> 0.70) with alternatives."""
    if len(selected) < 2:
        return selected

    memo = memo if memo is not None else {}

    for _ in range(_MAX_CORR_PASSES):
        try:
            corr = _corr_matrix(selected, ticker_data, memo)
            if corr is None or corr.isnull().all().all():
                break

            # Worst offending pair = max over the strict upper triangle. Vectorised;
            # the previous nested loop did O(n²) scalar .at[] lookups per pass.
            vals = corr.to_numpy(copy=True)
            vals[np.tril_indices_from(vals)] = np.nan
            if np.all(np.isnan(vals)):
                break
            i, j = np.unravel_index(np.nanargmax(vals), vals.shape)
            if not vals[i, j] > _CORR_THRESHOLD:
                break  # all pairs within threshold

            a, b = corr.index[i], corr.columns[j]
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
                # Restore rank order. The set is hoisted out of the comprehension —
                # it used to be rebuilt once per element of `ranked`.
                sel_set  = set(selected)
                selected = [s for s in ranked if s in sel_set]

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
    ticker_data: dict | None = None,
) -> list:
    """Ensure a minimum number of TSX-listed (CAD) picks per risk band."""
    if user_risk >= 7:
        return selected  # no minimum for aggressive

    min_cad = 3 if user_risk <= 3 else 2

    cad_count    = sum(1 for s in selected if _is_cad(s, ticker_data))
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

_MIN_WEIGHT_PCT = 1   # every pick that earns a page earns an allocation


def _compute_weights(selected: list, scores: dict) -> dict:
    """Return {sym: weight_pct} as whole percents summing to exactly 100.

    Uses the largest-remainder method on a 1% grid. The previous implementation
    rounded each weight to a 5% grid and dumped the entire residual on the largest
    position, which produced negative weights once the pick count grew: 23 picks
    cannot fit a 5% grid (23 x 5 = 115), so the residual went below zero.
    """
    n = len(selected)
    if n == 0:
        return {}
    if n >= 100:
        # Below the resolution of a whole-percent grid; split as evenly as possible.
        base = {s: 100 // n for s in selected}
        for s in selected[: 100 - sum(base.values())]:
            base[s] += 1
        return base

    qualities = [scores[s]["macro_adjusted_quality"] for s in selected]
    mean_q    = sum(qualities) / n or 1.0

    raw   = {s: (1.0 / n) * (scores[s]["macro_adjusted_quality"] / mean_q) for s in selected}
    total = sum(raw.values()) or 1.0
    pcts  = {s: raw[s] / total * 100 for s in selected}

    # Reserve the floor first, then apportion what's left by largest remainder so
    # the floor can never push the total past 100.
    floor_total = _MIN_WEIGHT_PCT * n
    spare       = 100 - floor_total
    scaled      = {s: pcts[s] / 100 * spare for s in selected}

    weights   = {s: _MIN_WEIGHT_PCT + int(scaled[s]) for s in selected}
    remaining = 100 - sum(weights.values())

    # Hand the leftover points to the largest fractional parts (ties by rank order).
    order = sorted(selected, key=lambda s: (-(scaled[s] - int(scaled[s])), selected.index(s)))
    for s in order[:remaining]:
        weights[s] += 1

    return weights


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
    is_etf: bool | None = None,
    is_cad: bool | None = None,
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
        "is_etf":                  (sym in ETF_TICKERS) if is_etf is None else is_etf,
        "currency":                ("CAD" if is_cad else "USD") if is_cad is not None else None,
        "risk_score":              rs.get("risk_score"),
        # Per-component sub-scores from risk_scorer.score_ticker — drives the
        # risk-score breakdown chart on the ticker page.
        "risk_score_components":   rs.get("components") or {},
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
    # Commissions are quoted in the listing currency, so CAD and USD fees are
    # tracked separately rather than summed into one misleading "CAD" total.
    trading_cad = 0.0
    trading_usd = 0.0
    total_fx    = 0.0
    total_etf_drag = 0.0
    etf_breakdown: list[dict] = []
    unknown_mer: list[str] = []

    for pick in picks:
        w    = pick.get("weight", 0)
        cost = pick.get("cost", {}) or {}
        pos  = amount * (w / 100)

        exp_ratio = cost.get("expense_ratio")          # None when unknown
        trading   = cost.get("trading_cost") or 0.0
        currency  = cost.get("trading_cost_currency") or "CAD"
        fx_applic = cost.get("fx_conversion_applicable", False)

        if currency == "CAD":
            trading_cad += trading
        else:
            trading_usd += trading

        total_fx += pos * 0.015 if fx_applic else 0.0

        if exp_ratio:
            drag = pos * exp_ratio
            total_etf_drag += drag
            etf_breakdown.append({
                "symbol":                  pick["symbol"],
                "mer_pct":                 f"{exp_ratio * 100:.2f}%",
                "annual_drag_on_position": round(drag, 2),
            })
        elif pick.get("is_etf"):
            unknown_mer.append(pick["symbol"])

    # Only meaningful if at least one ETF reported a real MER.
    if etf_breakdown:
        avg_mer = total_etf_drag / amount if amount > 0 else 0.0
        if avg_mer <= 0.001:
            eff = "Excellent"
        elif avg_mer <= 0.0025:
            eff = "Good"
        elif avg_mer <= 0.006:
            eff = "Fair"
        else:
            eff = "Poor"
    else:
        avg_mer = None
        eff     = None

    _platform_notes = {
        "questrade":    "ETF buys are free on Questrade. USD positions incur ~1.5% FX conversion.",
        "wealthsimple": "Commission-free on Wealthsimple. USD positions incur ~1.5% FX conversion.",
        "td_direct":    "All trades $9.99 on TD Direct. USD positions incur ~1.5% FX conversion.",
    }

    return {
        "assumed_portfolio_cad":       amount,
        "trading_cost_cad":            round(trading_cad, 2),
        "trading_cost_usd":            round(trading_usd, 2),
        "total_fx_conversion_cost":    round(total_fx, 2),
        "annual_etf_drag_cad":         round(total_etf_drag, 2) if etf_breakdown else None,
        "annual_etf_drag_pct":         round(avg_mer * 100, 4) if avg_mer is not None else None,
        "etf_cost_efficiency":         eff,
        "cost_efficiency_breakdown":   etf_breakdown,
        "etfs_missing_mer":            unknown_mer,
        "platform":                    platform,
        "note":                        _platform_notes.get(platform, ""),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _avg_portfolio_corr(corr) -> float | None:
    """Average pairwise correlation from a prebuilt matrix (upper triangle)."""
    if corr is None or corr.empty:
        return None
    try:
        vals  = corr.to_numpy()
        pairs = vals[np.triu_indices_from(vals, k=1)]
        pairs = pairs[~np.isnan(pairs)]
        return round(float(pairs.mean()), 4) if pairs.size else None
    except Exception:
        return None


def _empty_output(macro_context: dict) -> dict:
    return {
        "picks":               [],
        "sector_breakdown":    {},
        "currency_breakdown":  {"CAD": 0, "USD": 0},
        "avg_portfolio_corr":  None,
        "correlation_matrix":  None,
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
    print(f"  Commissions            : ${cost.get('trading_cost_cad', 0):.2f} CAD"
          f" + ${cost.get('trading_cost_usd', 0):.2f} USD")
    print(f"  FX conversion          : ${cost.get('total_fx_conversion_cost', 0):.2f} CAD")
    drag, drag_pct = cost.get("annual_etf_drag_cad"), cost.get("annual_etf_drag_pct")
    print(f"  Annual ETF drag        : "
          + (f"${drag:.2f} ({drag_pct:.3f}%)" if drag is not None else "not available"))
    if cost.get("etfs_missing_mer"):
        print(f"    (MER unavailable for: {', '.join(cost['etfs_missing_mer'])})")
    print(f"  Note: {cost.get('note', '')}")
