"""
Stock Market Research Tool — CLI entry point.

Usage examples:
    python main.py --risk 5
    python main.py --risk 3 --output report.pdf
    python main.py --risk 5 --amount 100000 --platform wealthsimple
    python main.py --risk 6 --universe-size 40
    python main.py --risk 7 --sectors Technology Energy
    python main.py --risk 5 --include NVDA --exclude TSLA
"""

import argparse
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor

# DuckDuckGo rate-limits more aggressively than Yahoo, so the research pool is
# deliberately smaller than the data-fetch pool.
_WEB_WORKERS = 4

logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("main")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="main.py",
        description="Generate a PDF stock market research report.",
    )
    p.add_argument("--risk", type=int, required=True,
                   help="Risk tolerance 1–10 (1 = conservative, 10 = aggressive)")
    p.add_argument("--output", default="stock_report.pdf",
                   help="Output PDF filename (default: stock_report.pdf)")
    p.add_argument("--amount", type=int, default=50_000,
                   help="Portfolio size in CAD for cost calculations (default: 50000)")
    p.add_argument("--platform", default="questrade",
                   choices=["questrade", "wealthsimple", "td_direct"],
                   help="Brokerage platform for fee estimates (default: questrade)")
    p.add_argument("--universe-size", type=int, default=30,
                   help="Number of tickers to analyse (default: 30, max: 85)")
    p.add_argument("--sectors", nargs="+", default=None,
                   help="Restrict universe to these sectors (space-separated)")
    p.add_argument("--include", nargs="+", default=None,
                   help="Force-include these symbols (space-separated)")
    p.add_argument("--exclude", nargs="+", default=None,
                   help="Force-exclude these symbols (space-separated)")
    p.add_argument("--no-cache", action="store_true",
                   help="Bypass the local market-data cache and always fetch fresh")
    p.add_argument("--verbose", action="store_true",
                   help="Show INFO-level log messages")
    return p


def _validate(args):
    if not 1 <= args.risk <= 10:
        sys.exit("Error: --risk must be between 1 and 10.")
    if not args.output.endswith(".pdf"):
        sys.exit("Error: --output must end in .pdf")
    if args.amount <= 0:
        sys.exit("Error: --amount must be a positive integer.")
    if not 10 <= args.universe_size <= 85:
        sys.exit("Error: --universe-size must be between 10 and 85.")


# ---------------------------------------------------------------------------
# Shared pipeline (steps 2–8): fetch → analyse → research → recommend → PDF
# ---------------------------------------------------------------------------

def _run_pipeline(tickers: list, args, log, t0: float, custom_mode: bool = False) -> None:
    """Execute steps 2–8 given a resolved ticker list."""

    def _step(msg: str):
        log(f"  [{time.time() - t0:5.1f}s]  {msg}")

    # ── 2. Fetch market data ─────────────────────────────────────────────────
    _step(f"Fetching data for {len(tickers)} tickers…")
    from data.fetcher import DataFetcher
    fetcher  = DataFetcher(use_cache=not getattr(args, "no_cache", False))

    if custom_mode:
        # Resolve symbols: retry with .TO suffix for TSX-listed tickers.
        # resolve_symbols hands back the data it fetched, so we don't fetch twice.
        resolution, prefetched = fetcher.resolve_symbols(tickers)
        resolved_tickers = []
        for orig in tickers:
            resolved = resolution.get(orig)
            if resolved is None:
                log(f"         ⚠ Could not fetch '{orig}' — skipped (try adding .TO for TSX stocks)")
            else:
                if resolved != orig:
                    log(f"         ℹ Resolved '{orig}' → '{resolved}'")
                resolved_tickers.append(resolved)
        tickers = resolved_tickers
        raw = {s: prefetched[s] for s in tickers if s in prefetched}
    else:
        raw = fetcher.fetch_all(tickers)

    spy_hist = fetcher.spy_history

    fetched = [s for s, d in raw.items()
               if d.get("history") is not None and not d["history"].empty]
    if len(fetched) < 1:
        raise RuntimeError("No tickers could be fetched. Check symbols and network connection.")
    _step(f"  ✓ {len(fetched)}/{len(tickers)} tickers fetched.")

    # ── 3. Analysis pipeline ─────────────────────────────────────────────────
    _step("Running analysis pipeline…")
    from analysis.risk_scorer  import score_ticker
    from analysis.risk_metrics import compute_risk_metrics
    from analysis.technical    import compute_technicals
    from analysis.fundamentals import compute_fundamentals
    from analysis.forecast     import compute_forecast
    from research.web_searcher import search_ticker as web_search
    from data.ticker_selector  import DISPLAY_NAMES

    risk_scores:  dict = {}
    risk_metrics: dict = {}
    technicals:   dict = {}
    fundamentals: dict = {}
    forecasts:    dict = {}
    sentiments:   dict = {}

    for sym in fetched:
        data = raw[sym]
        hist = data["history"]
        # technicals first: its return_1y feeds compute_risk_metrics, which would
        # otherwise recompute the same figure.
        technicals[sym]   = compute_technicals(hist)
        risk_scores[sym]  = score_ticker(data, spy_hist)
        risk_metrics[sym] = compute_risk_metrics(
            data, return_1y=technicals[sym].get("return_1y")
        )
        fundamentals[sym] = compute_fundamentals(
            data, spy_hist, args.platform, beta=risk_scores[sym].get("beta")
        )
        forecasts[sym]    = compute_forecast(data)

    _step(f"  ✓ Scored {len(fetched)} tickers.")

    # Free large DataFrames now that analysis is complete, and trim history.
    # 460 rows = 252 plotted days + the 200-day MA200 window that price_chart
    # computes before slicing to the last year. Trimming to 252 here left MA200
    # with only ~53 valid points, rendering it as a stub.
    for sym in fetched:
        d = raw[sym]
        for key in ("balance_sheet", "income_stmt", "recommendations",
                    "upgrades_downgrades", "funds_data"):
            d.pop(key, None)
        if d.get("history") is not None and not d["history"].empty:
            d["history"] = d["history"].tail(460)

    # ── 4. Web research (sentiment + risk factors) ───────────────────────────
    _step("Running web research (sentiment & risk factors)…")
    _NEUTRAL_SENTIMENT = {
        "raw_summary": None, "sentiment_score": 0.0,
        "sentiment_label": "Neutral", "risk_factors": [],
        "sources": [], "source_scores": [],
    }
    def _research(sym: str):
        name = DISPLAY_NAMES.get(sym, sym)
        try:
            return sym, web_search(sym, name)
        except Exception as exc:
            logger.warning("%s: web search failed: %s", sym, exc)
            return sym, dict(_NEUTRAL_SENTIMENT)

    # Network-bound and independent per ticker. Kept well below the fetch pool
    # size — DuckDuckGo throttles harder than Yahoo.
    done = 0
    with ThreadPoolExecutor(max_workers=min(_WEB_WORKERS, len(fetched))) as pool:
        for sym, result in pool.map(_research, fetched):
            sentiments[sym] = result
            done += 1
            if done % 5 == 0:
                _step(f"    {done}/{len(fetched)} web searches done…")

    _step("  ✓ Web research complete.")

    # ── 5. Macro context ─────────────────────────────────────────────────────
    _step("Fetching macro context…")
    from analysis.macro import fetch_macro_context
    macro_context = fetch_macro_context()
    log(f"         Regime: {macro_context['regime_description']}")

    # ── 6. Recommendation engine ─────────────────────────────────────────────
    _step("Building recommendations…")
    from recommendations.engine import recommend
    rec_result = recommend(
        tickers=fetched,
        ticker_data=raw,
        risk_scores=risk_scores,
        risk_metrics=risk_metrics,
        technicals=technicals,
        fundamentals=fundamentals,
        forecasts=forecasts,
        sentiments=sentiments,
        macro_context=macro_context,
        user_risk=args.risk,
        amount=args.amount,
        platform=args.platform,
        custom_mode=custom_mode,
    )
    picks = rec_result["picks"]
    if not picks:
        raise RuntimeError("No picks selected — try adjusting the risk level or universe.")
    _step(f"  ✓ {len(picks)} picks selected: {[p['symbol'] for p in picks]}")

    # ── 7. Scenario analysis ─────────────────────────────────────────────────
    _step("Running portfolio scenario analysis…")
    from analysis.scenarios import run_scenarios
    scenario_result = run_scenarios(picks, macro_context)
    _step(f"  ✓ Scenarios: crash {scenario_result['scenarios']['crash']['portfolio_return']:.1%} | "
          f"bull {scenario_result['scenarios']['bull_run']['portfolio_return']:.1%}")

    # ── 8. Generate PDF ──────────────────────────────────────────────────────
    _step("Generating PDF report…")
    from report.pdf_generator import generate_pdf
    generate_pdf(
        output_path=args.output,
        picks=picks,
        macro_context=macro_context,
        scenario_result=scenario_result,
        ticker_data=raw,
        risk_scores=risk_scores,
        risk_metrics=risk_metrics,
        rec_result=rec_result,
        user_risk=args.risk,
        amount=args.amount,
        platform=args.platform,
    )


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def run(args, log=print) -> None:
    """Universe-selection pipeline: select tickers, then run the full pipeline."""
    t0 = time.time()

    log(f"\nStock Market Research Tool")
    log(f"  Risk level  : {args.risk}/10")
    log(f"  Universe    : {args.universe_size} tickers")
    log(f"  Portfolio   : ${args.amount:,} CAD  |  {args.platform}")
    log(f"  Output      : {args.output}")
    log("")

    # ── 1. Select ticker universe ────────────────────────────────────────────
    log(f"  [{0.0:5.1f}s]  Selecting ticker universe…")
    from data.ticker_selector import select_tickers, AVAILABLE_SECTORS

    if args.sectors:
        invalid = [s for s in args.sectors if s not in AVAILABLE_SECTORS]
        if invalid:
            raise RuntimeError(f"Unknown sectors: {invalid}. Valid: {AVAILABLE_SECTORS}")

    tickers = select_tickers(
        size=args.universe_size,
        user_risk=args.risk,
        sectors=args.sectors,
        include=args.include,
        exclude=args.exclude,
    )
    log(f"         Universe: {tickers}")

    _run_pipeline(tickers, args, log, t0)

    log(f"\n  Done in {time.time() - t0:.1f}s  →  {args.output}\n")


def run_custom(symbols: list, args, log=print) -> None:
    """Custom-symbol pipeline: skip ticker selection and use the provided list."""
    t0 = time.time()

    tickers = [s.strip().upper() for s in symbols if s.strip()]
    if not tickers:
        raise RuntimeError("No valid symbols provided.")

    log(f"\nCustom Stock Analysis")
    log(f"  Symbols     : {tickers}")
    log(f"  Risk level  : {args.risk}/10")
    log(f"  Portfolio   : ${args.amount:,} CAD  |  {args.platform}")
    log(f"  Output      : {args.output}")
    log("")

    _run_pipeline(tickers, args, log, t0, custom_mode=True)

    log(f"\n  Done in {time.time() - t0:.1f}s  →  {args.output}\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = _build_parser()
    args   = parser.parse_args()
    args.universe_size = getattr(args, "universe_size",
                                  getattr(args, "universe-size", 30))

    if args.verbose:
        logging.getLogger().setLevel(logging.INFO)

    _validate(args)
    run(args)


if __name__ == "__main__":
    main()
