"""
Portfolio Scenario Analysis (Step 9, §14).

Applies four macro stress-test scenarios to a recommended portfolio and
computes estimated per-pick and portfolio-level returns for each.

Public API:
    run_scenarios(picks, macro_context=None) -> dict   (§14.4 output)
    portfolio_stats(picks)                  -> dict

Usage:
    from analysis.scenarios import run_scenarios, portfolio_stats
    result = run_scenarios(picks, macro_context)

    python -c "from analysis.scenarios import scenarios_demo; scenarios_demo()"
"""

import logging

from analysis.macro import SECTOR_SENSITIVITY
from data.ticker_selector import SECTOR_MAP

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# §14.1  Scenario definitions
# ---------------------------------------------------------------------------

_SCENARIO_LABELS = {
    "crash":     "Market Crash",
    "rate_hike": "Rate Hike (+1%)",
    "bull_run":  "Bull Run",
    "recession": "Recession",
}

_SCENARIO_DESCRIPTIONS = {
    "crash":     "Broad market drops 25% — beta-driven losses, floor at −50% per pick.",
    "rate_hike": "Interest rates rise 1% — sector sensitivity from macro matrix × 0.5.",
    "bull_run":  "Broad market rises 20% — beta-driven gains, cap at +60% per pick.",
    "recession": "GDP contraction — sector sensitivity from macro matrix × 0.8.",
}

# SECTOR_SENSITIVITY tuple indices (rates_rising, rates_falling, high_inflation, recession)
_IDX_RATES_RISING = 0
_IDX_RECESSION    = 3

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_scenarios(picks: list, macro_context: dict = None) -> dict:
    """Run four stress-test scenarios against the portfolio picks.

    Args:
        picks:         List of pick dicts from recommend() — §13.5 structure.
        macro_context: Unused at runtime (scenarios use SECTOR_SENSITIVITY
                       directly); accepted for API symmetry with other modules.

    Returns:
        §14.4 output dict.
    """
    stats = portfolio_stats(picks)

    scenarios = {}
    for key in ("crash", "rate_hike", "bull_run", "recession"):
        scenarios[key] = _run_one_scenario(key, picks)

    return {
        "portfolio_beta":   stats["portfolio_beta"],
        "portfolio_sharpe": stats["portfolio_sharpe"],
        "portfolio_max_dd": stats["portfolio_max_dd"],
        "scenarios":        scenarios,
    }


def portfolio_stats(picks: list) -> dict:
    """Compute portfolio-level beta, Sharpe, and max drawdown (§14.3).

    All three are weighted averages using each pick's portfolio weight.
    Beta fallback: 1.0 when missing (§18).

    Args:
        picks: List of pick dicts from recommend().

    Returns:
        {"portfolio_beta": float, "portfolio_sharpe": float, "portfolio_max_dd": float}
    """
    p_beta   = 0.0
    p_sharpe = 0.0
    p_max_dd = 0.0

    for pick in picks:
        w      = (pick.get("weight") or 0) / 100
        beta   = _get_beta(pick)
        sharpe = pick.get("sharpe_ratio") or 0.0
        max_dd = pick.get("max_drawdown") or 0.0

        p_beta   += w * beta
        p_sharpe += w * sharpe
        p_max_dd += w * max_dd

    return {
        "portfolio_beta":   round(p_beta,   4),
        "portfolio_sharpe": round(p_sharpe, 4),
        "portfolio_max_dd": round(p_max_dd, 4),
    }


# ---------------------------------------------------------------------------
# Internal — per-scenario computation
# ---------------------------------------------------------------------------

def _run_one_scenario(key: str, picks: list) -> dict:
    """Compute one scenario across all picks and return the scenario sub-dict."""
    if not picks:
        return {"portfolio_return": 0.0, "best_pick": None, "worst_pick": None, "per_pick": {}}

    per_pick: dict[str, float] = {}
    for pick in picks:
        per_pick[pick["symbol"]] = round(_estimate_move(key, pick), 4)

    portfolio_return = sum(
        (pick.get("weight") or 0) / 100 * per_pick[pick["symbol"]]
        for pick in picks
    )

    best_sym  = max(per_pick, key=per_pick.__getitem__)
    worst_sym = min(per_pick, key=per_pick.__getitem__)

    return {
        "portfolio_return": round(portfolio_return, 4),
        "best_pick":  {"symbol": best_sym,  "return": per_pick[best_sym]},
        "worst_pick": {"symbol": worst_sym, "return": per_pick[worst_sym]},
        "per_pick":   per_pick,
    }


def _estimate_move(scenario: str, pick: dict) -> float:
    """Estimate the price return for one pick under a given scenario (§14.2)."""
    beta   = _get_beta(pick)
    sector = SECTOR_MAP.get(pick.get("symbol", ""), "")
    sens   = SECTOR_SENSITIVITY.get(sector)  # tuple or None

    if scenario == "crash":
        # Beta-driven loss; floor at −50%
        return max(beta * -0.25, -0.50)

    if scenario == "rate_hike":
        # Sector rate-sensitivity × 0.5; 0.0 if sector unknown
        return sens[_IDX_RATES_RISING] * 0.5 if sens is not None else 0.0

    if scenario == "bull_run":
        # Beta-driven gain; cap at +60%
        return min(beta * 0.20, 0.60)

    if scenario == "recession":
        # Sector recession-sensitivity × 0.8; 0.0 if sector unknown
        return sens[_IDX_RECESSION] * 0.8 if sens is not None else 0.0

    return 0.0


def _get_beta(pick: dict) -> float:
    """Return beta from pick's fundamentals; fall back to 1.0 (§18)."""
    beta = (pick.get("fundamentals") or {}).get("beta")
    if beta is None or not isinstance(beta, (int, float)):
        return 1.0
    return float(beta)


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

def scenarios_demo(user_risk: int = 5) -> None:
    """Fetch live data, build a portfolio, run scenarios, and print results."""
    import logging as _log
    _log.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    from data.fetcher import DataFetcher
    from analysis.risk_scorer import score_ticker
    from analysis.risk_metrics import compute_risk_metrics
    from analysis.technical import compute_technicals
    from analysis.fundamentals import compute_fundamentals
    from analysis.forecast import compute_forecast
    from analysis.macro import fetch_macro_context
    from recommendations.engine import recommend

    DEMO_TICKERS = [
        "AAPL", "MSFT", "GOOGL",
        "RY.TO", "TD.TO", "JPM",
        "ENB.TO", "XOM",
        "JNJ", "WMT",
        "XIU.TO", "ZAG.TO", "SPY",
    ]

    print("Fetching data …")
    fetcher  = DataFetcher()
    spy_hist = fetcher.spy_history
    raw      = fetcher.fetch_all(DEMO_TICKERS)

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
    symbols = [p["symbol"] for p in picks]
    print(f"\nPortfolio (risk={user_risk}): {symbols}\n")

    out = run_scenarios(picks, macro_ctx)

    print(f"Portfolio beta    : {out['portfolio_beta']:.2f}")
    print(f"Portfolio Sharpe  : {out['portfolio_sharpe']:.2f}")
    print(f"Portfolio max DD  : {out['portfolio_max_dd']:.1%}")

    print(f"\n{'Scenario':<18} {'Portfolio':>10}   {'Best pick':<22} {'Worst pick':<22}")
    print(f"{'-'*18} {'-'*10}   {'-'*22} {'-'*22}")

    for key in ("crash", "rate_hike", "bull_run", "recession"):
        s    = out["scenarios"][key]
        bp   = s["best_pick"]
        wp   = s["worst_pick"]
        bstr = f"{bp['symbol']} ({bp['return']:+.1%})" if bp else "n/a"
        wstr = f"{wp['symbol']} ({wp['return']:+.1%})" if wp else "n/a"
        print(
            f"{_SCENARIO_LABELS[key]:<18} {s['portfolio_return']:>+10.1%}"
            f"   {bstr:<22} {wstr:<22}"
        )

    print("\nPer-pick breakdown:")
    hdr = f"  {'Symbol':<10}" + "".join(f" {_SCENARIO_LABELS[k]:>14}" for k in ("crash","rate_hike","bull_run","recession"))
    print(hdr)
    print("  " + "-" * (10 + 15 * 4))
    for pick in picks:
        sym  = pick["symbol"]
        row  = f"  {sym:<10}"
        for key in ("crash", "rate_hike", "bull_run", "recession"):
            v = out["scenarios"][key]["per_pick"].get(sym, 0.0)
            row += f" {v:>+14.1%}"
        print(row)
