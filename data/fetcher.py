"""
Data fetcher module (Step 2).

Fetches market data from yfinance for a list of ticker symbols and returns a
unified dict keyed by symbol.  One SPY baseline fetch happens at construction
time for fallback beta calculations.

Usage:
    from data.fetcher import DataFetcher

    fetcher = DataFetcher()
    data = fetcher.fetch_all(["AAPL", "RY.TO", "XIU.TO"])
    # data["AAPL"]["history"]  → DataFrame
    # data["AAPL"]["info"]     → dict

    # Single ticker:
    d = fetcher.fetch_ticker("MSFT")
    print(d.keys())

Output structure per ticker (§5.4):
    {
      "symbol":            str,
      "is_etf":            bool,
      "info":              dict,
      "history":           DataFrame,         # 5yr OHLCV
      "recommendations":   DataFrame | None,
      "upgrades_downgrades": DataFrame | None,
      "balance_sheet":     DataFrame | None,  # stocks only
      "income_stmt":       DataFrame | None,  # stocks only
      "cashflow":          DataFrame | None,  # stocks only
      "dividends":         Series   | None,   # stocks only
      "funds_data":        object   | None,   # ETFs only
    }

SPY baseline (§5.2):
    fetcher.spy_history  → 1yr Close Series used to calculate beta for tickers
    where info['beta'] is missing.
"""

import logging
import warnings

import yfinance as yf

from data.ticker_selector import TICKER_POOL

logger = logging.getLogger(__name__)

# Tickers flagged as ETF in the pool (derived at import time so fetcher
# doesn't need to repeat the lookup on every call).
_ETF_SYMBOLS = {sym for sym, meta in TICKER_POOL.items() if meta["type"] == "ETF"}


class DataFetcher:
    """Fetches yfinance data for a universe of tickers."""

    def __init__(self):
        self.spy_history = self._fetch_spy_baseline()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_all(self, symbols: list[str]) -> dict:
        """Fetch data for every symbol in *symbols*.

        Failures are logged and skipped; the returned dict only contains
        tickers that were fetched successfully and have non-empty history.
        """
        results = {}
        for symbol in symbols:
            try:
                data = self._fetch_one(symbol)
                if data is not None:
                    results[symbol] = data
            except Exception as exc:
                logger.warning("Skipping %s — unexpected error: %s", symbol, exc)
        return results

    def fetch_ticker(self, symbol: str) -> dict | None:
        """Fetch a single ticker.  Returns None on failure."""
        try:
            return self._fetch_one(symbol)
        except Exception as exc:
            logger.warning("Failed to fetch %s: %s", symbol, exc)
            return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch_spy_baseline(self):
        """Fetch 1yr SPY history for beta fallback (§5.2).

        Returns a Series of daily Close prices, or None if the fetch fails.
        """
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                spy = yf.Ticker("SPY")
                hist = spy.history(period="1y")
            if hist.empty:
                logger.warning("SPY baseline history is empty; beta fallback disabled.")
                return None
            return hist["Close"]
        except Exception as exc:
            logger.warning("Could not fetch SPY baseline: %s", exc)
            return None

    def _fetch_one(self, symbol: str) -> dict | None:
        """Core per-ticker fetch.  Returns None if history is empty."""
        is_etf = symbol in _ETF_SYMBOLS

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ticker = yf.Ticker(symbol)

            # ── Always fetched ──────────────────────────────────────────
            history = ticker.history(period="5y")
            if history.empty:
                logger.warning("Skipping %s — empty price history.", symbol)
                return None

            info = self._safe_info(ticker)
            recommendations = self._safe_df(ticker, "recommendations")
            upgrades_downgrades = self._safe_df(ticker, "upgrades_downgrades")

            # ── Stock-only ──────────────────────────────────────────────
            balance_sheet = None
            income_stmt = None
            cashflow = None
            dividends = None

            if not is_etf:
                balance_sheet = self._safe_df(ticker, "balance_sheet")
                income_stmt = self._safe_df(ticker, "income_stmt")
                cashflow = self._safe_df(ticker, "cashflow")

            # dividends fetched for both stocks and ETFs (ETFs use for distribution history)
            dividends = self._safe_dividends(ticker)

            # ── ETF-only ────────────────────────────────────────────────
            funds_data = None
            if is_etf:
                funds_data = self._safe_funds_data(ticker, symbol)

        return {
            "symbol": symbol,
            "is_etf": is_etf,
            "info": info,
            "history": history,
            "recommendations": recommendations,
            "upgrades_downgrades": upgrades_downgrades,
            "balance_sheet": balance_sheet,
            "income_stmt": income_stmt,
            "cashflow": cashflow,
            "dividends": dividends,
            "funds_data": funds_data,
        }

    # ------------------------------------------------------------------
    # Safe accessors — never propagate exceptions to the caller
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_info(ticker) -> dict:
        try:
            info = ticker.info
            return info if isinstance(info, dict) else {}
        except Exception as exc:
            logger.debug("Could not fetch info for %s: %s", ticker.ticker, exc)
            return {}

    @staticmethod
    def _safe_df(ticker, attr: str):
        """Return a DataFrame attribute from a yfinance Ticker, or None."""
        try:
            df = getattr(ticker, attr)
            if df is None or (hasattr(df, "empty") and df.empty):
                return None
            return df
        except Exception as exc:
            logger.debug("Could not fetch %s.%s: %s", ticker.ticker, attr, exc)
            return None

    @staticmethod
    def _safe_dividends(ticker):
        """Return the dividends Series, or None if unavailable/empty."""
        try:
            divs = ticker.dividends
            if divs is None or divs.empty:
                return None
            return divs
        except Exception as exc:
            logger.debug("Could not fetch dividends for %s: %s", ticker.ticker, exc)
            return None

    @staticmethod
    def _safe_funds_data(ticker, symbol: str):
        """Return funds_data for ETFs, or None if unavailable."""
        try:
            fd = ticker.funds_data
            return fd
        except Exception as exc:
            logger.debug("Could not fetch funds_data for %s: %s", symbol, exc)
            return None


# ---------------------------------------------------------------------------
# CLI demo  —  python -c "from data.fetcher import DataFetcher; d = DataFetcher(); print(d.fetch_ticker('AAPL').keys())"
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

    symbols = sys.argv[1:] if len(sys.argv) > 1 else ["AAPL", "RY.TO", "XIU.TO"]
    print(f"Fetching: {symbols}\n")

    fetcher = DataFetcher()
    data = fetcher.fetch_all(symbols)

    for sym, d in data.items():
        rows = len(d["history"])
        info_keys = len(d["info"])
        rec_rows = len(d["recommendations"]) if d["recommendations"] is not None else 0
        ud_rows = len(d["upgrades_downgrades"]) if d["upgrades_downgrades"] is not None else 0
        etf_tag = " [ETF]" if d["is_etf"] else ""
        print(f"{sym}{etf_tag}")
        print(f"  history:            {rows} rows")
        print(f"  info keys:          {info_keys}")
        print(f"  recommendations:    {rec_rows} rows")
        print(f"  upgrades/downgrades:{ud_rows} rows")
        if not d["is_etf"]:
            bs = "✓" if d["balance_sheet"] is not None else "–"
            is_ = "✓" if d["income_stmt"] is not None else "–"
            cf = "✓" if d["cashflow"] is not None else "–"
            dv = len(d["dividends"]) if d["dividends"] is not None else "–"
            print(f"  balance_sheet:      {bs}")
            print(f"  income_stmt:        {is_}")
            print(f"  cashflow:           {cf}")
            print(f"  dividends:          {dv} entries")
        else:
            fd = "✓" if d["funds_data"] is not None else "–"
            print(f"  funds_data:         {fd}")
        print()
