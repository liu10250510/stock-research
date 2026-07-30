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
      "history":           DataFrame,         # 5yr OHLCV (+ Dividends column)
      "recommendations":   DataFrame | None,  # recommendationTrend: analyst counts
      "upgrades_downgrades": DataFrame | None,
      "balance_sheet":     DataFrame | None,  # stocks only
      "income_stmt":       DataFrame | None,  # stocks only
      "dividends":         Series   | None,   # derived from history
      "funds_data":        object   | None,   # ETFs only
    }

Fetches run on a thread pool and are cached to disk for a short TTL
(see data/market_cache.py). Pass DataFetcher(use_cache=False) to force fresh data.

SPY baseline (§5.2):
    fetcher.spy_history  → 1yr Close Series used to calculate beta for tickers
    where info['beta'] is missing.
"""

import logging
import time
import warnings
from concurrent.futures import ThreadPoolExecutor

import yfinance as yf

from data import market_cache
from data.ticker_selector import EXCHANGE_MAP, TICKER_POOL

logger = logging.getLogger(__name__)

# Tickers flagged as ETF in the pool (derived at import time so fetcher
# doesn't need to repeat the lookup on every call).
_ETF_SYMBOLS = {sym for sym, meta in TICKER_POOL.items() if meta["type"] == "ETF"}

# The work is entirely network-bound, so threads are the right tool. 8 is a
# deliberate compromise: enough for a large speedup, low enough that Yahoo does
# not start returning 429s (which yfinance answers with a costly cookie/crumb
# re-acquisition and request replay).
_MAX_WORKERS = 8

# Yahoo exchange codes that mean "listed in Toronto".
_TSX_EXCHANGE_CODES = {"TOR", "TSE", "TSX", "VAN", "CNQ", "NEO"}


def _resolve_exchange(symbol: str, info: dict) -> str:
    """Return "TSX" or "NYSE" for costing/currency purposes.

    Uses Yahoo's own exchange/currency fields first; falls back to the static
    EXCHANGE_MAP, then to the .TO suffix. The static map only covers the 86-name
    pool, so custom symbols would otherwise all be treated as USD-listed.
    """
    code = (info.get("exchange") or "").upper()
    if code in _TSX_EXCHANGE_CODES:
        return "TSX"
    if code:
        return "NYSE"
    if (info.get("currency") or "").upper() == "CAD":
        return "TSX"
    if symbol in EXCHANGE_MAP:
        return EXCHANGE_MAP[symbol]
    return "TSX" if symbol.upper().endswith(".TO") else "NYSE"

# One retry only — a transient blip shouldn't silently drop a ticker from the
# report, but a genuinely bad symbol shouldn't cost two full round trips either.
_RETRIES = 1
_RETRY_BACKOFF = 1.0


class DataFetcher:
    """Fetches yfinance data for a universe of tickers."""

    def __init__(self, use_cache: bool = True):
        self.use_cache = use_cache
        self._spy_history = None
        self._spy_fetched = False

    @property
    def spy_history(self):
        """1yr SPY Close series, fetched on first access.

        Lazy because callers that only need per-ticker data (e.g. the
        /api/performance endpoint) would otherwise pay for this round trip.
        """
        if not self._spy_fetched:
            self._spy_history = self._fetch_spy_baseline()
            self._spy_fetched = True
        return self._spy_history

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_all(self, symbols: list[str]) -> dict:
        """Fetch data for every symbol in *symbols*.

        Failures are logged and skipped; the returned dict only contains
        tickers that were fetched successfully and have non-empty history.
        """
        if not symbols:
            return {}

        # Warm yfinance's process-global cookie/crumb on this thread first, so the
        # pool doesn't have 8 workers racing to acquire it simultaneously.
        self._warm_session()

        results = {}
        workers = min(_MAX_WORKERS, len(symbols))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(self._fetch_one, s): s for s in symbols}
            for fut in futures:
                symbol = futures[fut]
                try:
                    data = fut.result()
                    if data is not None:
                        results[symbol] = data
                except Exception as exc:
                    logger.warning("Skipping %s — unexpected error: %s", symbol, exc)

        # Preserve the caller's ordering, which fetch order no longer guarantees.
        return {s: results[s] for s in symbols if s in results}

    @staticmethod
    def _warm_session() -> None:
        """Force one cheap request so the shared cookie/crumb is cached."""
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                yf.Ticker("SPY").history(period="1d")
        except Exception as exc:
            logger.debug("Session warm-up failed (continuing): %s", exc)

    def fetch_ticker(self, symbol: str) -> dict | None:
        """Fetch a single ticker.  Returns None on failure."""
        try:
            return self._fetch_one(symbol)
        except Exception as exc:
            logger.warning("Failed to fetch %s: %s", symbol, exc)
            return None

    def resolve_symbols(self, symbols: list[str]) -> tuple[dict[str, str], dict]:
        """Resolve symbols that may be missing the TSX '.TO' suffix.

        For each symbol that fails to fetch, retries with '.TO' appended.

        Returns ``(resolution, data)`` where *resolution* maps
        {original_symbol: resolved_symbol} for every symbol that resolved, and
        *data* maps {resolved_symbol: ticker_data} for the fetches already
        performed here — so the caller does not have to fetch them again.
        Symbols that fail both variants are omitted from both dicts.
        """
        self._warm_session()

        def _resolve_one(sym: str):
            data = self._fetch_one(sym)
            if data is not None:
                return sym, sym, data
            # Try with .TO suffix (common for TSX-listed Canadian securities)
            if not sym.endswith(".TO"):
                candidate = sym + ".TO"
                try:
                    data = self._fetch_one(candidate)
                    if data is not None:
                        logger.info("Resolved %s → %s", sym, candidate)
                        return sym, candidate, data
                except Exception:
                    pass
            logger.warning("Could not resolve symbol: %s (tried with/without .TO)", sym)
            return sym, None, None

        resolved: dict[str, str] = {}
        fetched: dict = {}
        workers = min(_MAX_WORKERS, len(symbols)) or 1
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for orig, target, data in pool.map(_resolve_one, symbols):
                if target is not None:
                    resolved[orig] = target
                    fetched[target] = data
        return resolved, fetched

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
        """Core per-ticker fetch, cached and retried.  Returns None if unavailable."""
        if self.use_cache:
            hit = market_cache.get(symbol)
            if hit is not None:
                logger.debug("Cache hit for %s", symbol)
                return hit

        data = None
        for attempt in range(_RETRIES + 1):
            data = self._fetch_one_uncached(symbol)
            if data is not None:
                break
            if attempt < _RETRIES:
                logger.debug("Retrying %s after transient failure", symbol)
                time.sleep(_RETRY_BACKOFF)

        # Only successful fetches are cached; a miss stays a miss.
        if data is not None and self.use_cache:
            market_cache.put(symbol, data)
        return data

    def _fetch_one_uncached(self, symbol: str) -> dict | None:
        """Single fetch attempt.  Returns None if history is empty."""
        # Provisional; refined from info below once we have it. Pool membership
        # alone is wrong for custom mode, which accepts arbitrary symbols.
        is_etf = symbol in _ETF_SYMBOLS

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ticker = yf.Ticker(symbol)

            # ── Always fetched ──────────────────────────────────────────
            # actions=True (the default) means this response already carries the
            # Dividends column, so no separate dividends request is needed.
            history = ticker.history(period="5y")
            if history.empty:
                logger.warning("Skipping %s — empty price history.", symbol)
                return None

            info = self._safe_info(ticker)

            # Prefer Yahoo's own classification over pool membership, so custom
            # symbols outside the 86-name pool (e.g. VFV.TO) aren't analysed as
            # stocks and billed as USD.
            quote_type = (info.get("quoteType") or "").upper()
            if quote_type in ("ETF", "MUTUALFUND"):
                is_etf = True
            elif quote_type == "EQUITY":
                is_etf = False

            currency = (info.get("currency") or "").upper() or None
            exchange = _resolve_exchange(symbol, info)

            # recommendationTrend — current analyst buy/hold/sell counts. This is the
            # authoritative consensus source; upgrades_downgrades is only a log of
            # rating *changes* and is used solely for 30-day momentum.
            recommendations = self._safe_df(ticker, "recommendations")
            upgrades_downgrades = self._safe_df(ticker, "upgrades_downgrades")

            # ── Stock-only ──────────────────────────────────────────────
            balance_sheet = None
            income_stmt = None

            if not is_etf:
                balance_sheet = self._safe_df(ticker, "balance_sheet")
                income_stmt = self._safe_df(ticker, "income_stmt")

            # Derived from the history above — for both stocks and ETFs
            # (ETFs use it for distribution history).
            dividends = self._dividends_from_history(history)

            # ── ETF-only ────────────────────────────────────────────────
            funds_data = None
            if is_etf:
                funds_data = self._safe_funds_data(ticker, symbol)

        return {
            "symbol": symbol,
            "is_etf": is_etf,
            "currency": currency,
            "exchange": exchange,
            "info": info,
            "history": history,
            "recommendations": recommendations,
            "upgrades_downgrades": upgrades_downgrades,
            "balance_sheet": balance_sheet,
            "income_stmt": income_stmt,
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
    def _dividends_from_history(history):
        """Extract the dividends Series from an already-fetched history frame.

        Reading the Dividends column avoids ticker.dividends, which in yfinance
        re-downloads the ticker's *entire* listed price history (period="max") —
        15–20k daily bars for long-lived names like KO or IBM. Consumers only look
        back 5 years, which the history frame already covers.
        """
        try:
            if "Dividends" not in history.columns:
                return None
            divs = history["Dividends"]
            divs = divs[divs > 0]
            return divs if not divs.empty else None
        except Exception as exc:
            logger.debug("Could not derive dividends from history: %s", exc)
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
        ud_rows = len(d["upgrades_downgrades"]) if d["upgrades_downgrades"] is not None else 0
        etf_tag = " [ETF]" if d["is_etf"] else ""
        print(f"{sym}{etf_tag}")
        print(f"  history:            {rows} rows")
        print(f"  info keys:          {info_keys}")
        print(f"  upgrades/downgrades:{ud_rows} rows")
        dv = len(d["dividends"]) if d["dividends"] is not None else "–"
        print(f"  dividends:          {dv} entries")
        if not d["is_etf"]:
            bs = "✓" if d["balance_sheet"] is not None else "–"
            is_ = "✓" if d["income_stmt"] is not None else "–"
            print(f"  balance_sheet:      {bs}")
            print(f"  income_stmt:        {is_}")
        else:
            fd = "✓" if d["funds_data"] is not None else "–"
            print(f"  funds_data:         {fd}")
        print()
