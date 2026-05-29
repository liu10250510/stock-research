"""
Ticker selection module.

Maintains a pool of ~85 popular stocks and ETFs tradable on Canadian platforms
(Questrade, Wealthsimple Trade, TD Direct Investing) across 14 sectors.
Provides select_tickers() to build a risk-aware, quality-ranked subset.

Selection logic (two layers):
  1. Sector allocation  — sectors receive slots proportional to (pool_size ×
     sector_risk_affinity), so conservative users get more Bond ETF / Utilities
     slots and aggressive users get more Technology / Materials slots.
  2. Within-sector ranking  — tickers are sorted by (risk_fit, priority), where
     risk_fit = |ticker_risk_tier − user_target_tier|. Tickers whose risk tier
     matches the user's profile rise to the top; priority breaks ties by
     popularity/liquidity.

Usage:
    from data.ticker_selector import select_tickers, TICKER_POOL

    tickers = select_tickers(user_risk=5)                          # default 30, moderate
    tickers = select_tickers(user_risk=2, size=40)                 # conservative, 40 tickers
    tickers = select_tickers(user_risk=8, sectors=["Technology"])  # aggressive, one sector
    tickers = select_tickers(user_risk=5, include=["NVDA"], exclude=["TSLA"])

CLI demo:
    python -m data.ticker_selector --risk 5         # show default 30-ticker selection
    python -m data.ticker_selector --risk 2         # conservative selection
    python -m data.ticker_selector --risk 8         # aggressive selection
    python -m data.ticker_selector --risk 5 --size 40
    python -m data.ticker_selector --risk 7 --sectors Technology Energy
    python -m data.ticker_selector --list-all       # show full 85-ticker pool
"""

from collections import defaultdict
from math import floor


# ---------------------------------------------------------------------------
# Ticker pool
#
# Fields per ticker:
#   name       — display name
#   sector     — one of AVAILABLE_SECTORS
#   type       — "Stock" or "ETF"
#   exchange   — "TSX", "NYSE", or "NASDAQ"
#   risk_tier  — 1=conservative, 2=moderate, 3=aggressive (fallback label only;
#                the risk scorer overrides this with an algorithmic score)
#   priority   — within-sector ranking; lower = more popular (used for selection)
# ---------------------------------------------------------------------------

TICKER_POOL = {
    # ── Financials ──────────────────────────────────────────────────────────
    "RY.TO":     {"name": "Royal Bank of Canada",              "sector": "Financials",             "type": "Stock", "exchange": "TSX",    "risk_tier": 1, "priority": 1},
    "TD.TO":     {"name": "TD Bank",                           "sector": "Financials",             "type": "Stock", "exchange": "TSX",    "risk_tier": 1, "priority": 2},
    "BNS.TO":    {"name": "Bank of Nova Scotia",               "sector": "Financials",             "type": "Stock", "exchange": "TSX",    "risk_tier": 1, "priority": 3},
    "BMO.TO":    {"name": "Bank of Montreal",                  "sector": "Financials",             "type": "Stock", "exchange": "TSX",    "risk_tier": 1, "priority": 4},
    "CM.TO":     {"name": "CIBC",                              "sector": "Financials",             "type": "Stock", "exchange": "TSX",    "risk_tier": 2, "priority": 5},
    "MFC.TO":    {"name": "Manulife Financial",                "sector": "Financials",             "type": "Stock", "exchange": "TSX",    "risk_tier": 2, "priority": 6},
    "JPM":       {"name": "JPMorgan Chase",                    "sector": "Financials",             "type": "Stock", "exchange": "NYSE",   "risk_tier": 2, "priority": 7},
    "BAC":       {"name": "Bank of America",                   "sector": "Financials",             "type": "Stock", "exchange": "NYSE",   "risk_tier": 2, "priority": 8},
    "V":         {"name": "Visa",                              "sector": "Financials",             "type": "Stock", "exchange": "NYSE",   "risk_tier": 2, "priority": 9},
    "MA":        {"name": "Mastercard",                        "sector": "Financials",             "type": "Stock", "exchange": "NYSE",   "risk_tier": 2, "priority": 10},

    # ── Technology ──────────────────────────────────────────────────────────
    "MSFT":      {"name": "Microsoft",                         "sector": "Technology",             "type": "Stock", "exchange": "NASDAQ", "risk_tier": 2, "priority": 1},
    "AAPL":      {"name": "Apple",                             "sector": "Technology",             "type": "Stock", "exchange": "NASDAQ", "risk_tier": 2, "priority": 2},
    "NVDA":      {"name": "NVIDIA",                            "sector": "Technology",             "type": "Stock", "exchange": "NASDAQ", "risk_tier": 3, "priority": 3},
    "GOOGL":     {"name": "Alphabet (Google)",                 "sector": "Technology",             "type": "Stock", "exchange": "NASDAQ", "risk_tier": 2, "priority": 4},
    "META":      {"name": "Meta (Facebook)",                   "sector": "Technology",             "type": "Stock", "exchange": "NASDAQ", "risk_tier": 3, "priority": 5},
    "SHOP.TO":   {"name": "Shopify",                           "sector": "Technology",             "type": "Stock", "exchange": "TSX",    "risk_tier": 3, "priority": 6},
    "AMD":       {"name": "Advanced Micro Devices",            "sector": "Technology",             "type": "Stock", "exchange": "NASDAQ", "risk_tier": 3, "priority": 7},
    "CSU.TO":    {"name": "Constellation Software",            "sector": "Technology",             "type": "Stock", "exchange": "TSX",    "risk_tier": 2, "priority": 8},
    "CRM":       {"name": "Salesforce",                        "sector": "Technology",             "type": "Stock", "exchange": "NYSE",   "risk_tier": 2, "priority": 9},
    "ORCL":      {"name": "Oracle",                            "sector": "Technology",             "type": "Stock", "exchange": "NYSE",   "risk_tier": 2, "priority": 10},
    "IBM":       {"name": "IBM",                              "sector": "Technology",             "type": "Stock", "exchange": "NYSE",   "risk_tier": 2, "priority": 11},

    # ── Energy ──────────────────────────────────────────────────────────────
    "ENB.TO":    {"name": "Enbridge",                          "sector": "Energy",                 "type": "Stock", "exchange": "TSX",    "risk_tier": 1, "priority": 1},
    "CNQ.TO":    {"name": "Canadian Natural Resources",        "sector": "Energy",                 "type": "Stock", "exchange": "TSX",    "risk_tier": 2, "priority": 2},
    "SU.TO":     {"name": "Suncor Energy",                     "sector": "Energy",                 "type": "Stock", "exchange": "TSX",    "risk_tier": 2, "priority": 3},
    "TRP.TO":    {"name": "TC Energy",                         "sector": "Energy",                 "type": "Stock", "exchange": "TSX",    "risk_tier": 2, "priority": 4},
    "XOM":       {"name": "ExxonMobil",                        "sector": "Energy",                 "type": "Stock", "exchange": "NYSE",   "risk_tier": 2, "priority": 5},
    "CVX":       {"name": "Chevron",                           "sector": "Energy",                 "type": "Stock", "exchange": "NYSE",   "risk_tier": 2, "priority": 6},
    "COP":       {"name": "ConocoPhillips",                    "sector": "Energy",                 "type": "Stock", "exchange": "NYSE",   "risk_tier": 3, "priority": 7},
    "SLB":       {"name": "SLB (Schlumberger)",                "sector": "Energy",                 "type": "Stock", "exchange": "NYSE",   "risk_tier": 3, "priority": 8},

    # ── Consumer Staples ────────────────────────────────────────────────────
    "WMT":       {"name": "Walmart",                           "sector": "Consumer Staples",       "type": "Stock", "exchange": "NYSE",   "risk_tier": 1, "priority": 1},
    "PG":        {"name": "Procter & Gamble",                  "sector": "Consumer Staples",       "type": "Stock", "exchange": "NYSE",   "risk_tier": 1, "priority": 2},
    "KO":        {"name": "Coca-Cola",                         "sector": "Consumer Staples",       "type": "Stock", "exchange": "NYSE",   "risk_tier": 1, "priority": 3},
    "COST":      {"name": "Costco",                            "sector": "Consumer Staples",       "type": "Stock", "exchange": "NASDAQ", "risk_tier": 2, "priority": 4},
    "L.TO":      {"name": "Loblaw Companies",                  "sector": "Consumer Staples",       "type": "Stock", "exchange": "TSX",    "risk_tier": 2, "priority": 5},
    "ATD.TO":    {"name": "Alimentation Couche-Tard",          "sector": "Consumer Staples",       "type": "Stock", "exchange": "TSX",    "risk_tier": 2, "priority": 6},
    "MRU.TO":    {"name": "Metro Inc.",                        "sector": "Consumer Staples",       "type": "Stock", "exchange": "TSX",    "risk_tier": 1, "priority": 7},

    # ── Consumer Discretionary ──────────────────────────────────────────────
    "AMZN":      {"name": "Amazon",                            "sector": "Consumer Discretionary", "type": "Stock", "exchange": "NASDAQ", "risk_tier": 2, "priority": 1},
    "TSLA":      {"name": "Tesla",                             "sector": "Consumer Discretionary", "type": "Stock", "exchange": "NASDAQ", "risk_tier": 3, "priority": 2},
    "MCD":       {"name": "McDonald's",                        "sector": "Consumer Discretionary", "type": "Stock", "exchange": "NYSE",   "risk_tier": 1, "priority": 3},
    "HD":        {"name": "Home Depot",                        "sector": "Consumer Discretionary", "type": "Stock", "exchange": "NYSE",   "risk_tier": 2, "priority": 4},
    "NKE":       {"name": "Nike",                              "sector": "Consumer Discretionary", "type": "Stock", "exchange": "NYSE",   "risk_tier": 2, "priority": 5},
    "SBUX":      {"name": "Starbucks",                         "sector": "Consumer Discretionary", "type": "Stock", "exchange": "NASDAQ", "risk_tier": 2, "priority": 6},

    # ── Healthcare ──────────────────────────────────────────────────────────
    "JNJ":       {"name": "Johnson & Johnson",                 "sector": "Healthcare",             "type": "Stock", "exchange": "NYSE",   "risk_tier": 1, "priority": 1},
    "LLY":       {"name": "Eli Lilly",                         "sector": "Healthcare",             "type": "Stock", "exchange": "NYSE",   "risk_tier": 2, "priority": 2},
    "UNH":       {"name": "UnitedHealth Group",                "sector": "Healthcare",             "type": "Stock", "exchange": "NYSE",   "risk_tier": 2, "priority": 3},
    "ABBV":      {"name": "AbbVie",                            "sector": "Healthcare",             "type": "Stock", "exchange": "NYSE",   "risk_tier": 2, "priority": 4},
    "PFE":       {"name": "Pfizer",                            "sector": "Healthcare",             "type": "Stock", "exchange": "NYSE",   "risk_tier": 2, "priority": 5},
    "MRK":       {"name": "Merck",                             "sector": "Healthcare",             "type": "Stock", "exchange": "NYSE",   "risk_tier": 1, "priority": 6},
    "ABT":       {"name": "Abbott Laboratories",               "sector": "Healthcare",             "type": "Stock", "exchange": "NYSE",   "risk_tier": 2, "priority": 7},

    # ── Industrials ─────────────────────────────────────────────────────────
    "CNR.TO":    {"name": "CN Rail",                           "sector": "Industrials",            "type": "Stock", "exchange": "TSX",    "risk_tier": 1, "priority": 1},
    "CP.TO":     {"name": "CP Rail",                           "sector": "Industrials",            "type": "Stock", "exchange": "TSX",    "risk_tier": 2, "priority": 2},
    "CAT":       {"name": "Caterpillar",                       "sector": "Industrials",            "type": "Stock", "exchange": "NYSE",   "risk_tier": 2, "priority": 3},
    "HON":       {"name": "Honeywell",                         "sector": "Industrials",            "type": "Stock", "exchange": "NASDAQ", "risk_tier": 2, "priority": 4},
    "GE":        {"name": "GE Aerospace",                      "sector": "Industrials",            "type": "Stock", "exchange": "NYSE",   "risk_tier": 2, "priority": 5},
    "WSP.TO":    {"name": "WSP Global",                        "sector": "Industrials",            "type": "Stock", "exchange": "TSX",    "risk_tier": 2, "priority": 6},

    # ── Materials ────────────────────────────────────────────────────────────
    "ABX.TO":    {"name": "Barrick Gold",                      "sector": "Materials",              "type": "Stock", "exchange": "TSX",    "risk_tier": 3, "priority": 1},
    "AEM.TO":    {"name": "Agnico Eagle Mines",                "sector": "Materials",              "type": "Stock", "exchange": "TSX",    "risk_tier": 3, "priority": 2},
    "WPM.TO":    {"name": "Wheaton Precious Metals",           "sector": "Materials",              "type": "Stock", "exchange": "TSX",    "risk_tier": 3, "priority": 3},
    "TECK-B.TO": {"name": "Teck Resources",                    "sector": "Materials",              "type": "Stock", "exchange": "TSX",    "risk_tier": 3, "priority": 4},
    "NUE":       {"name": "Nucor Corporation",                 "sector": "Materials",              "type": "Stock", "exchange": "NYSE",   "risk_tier": 2, "priority": 5},

    # ── Utilities ────────────────────────────────────────────────────────────
    "FTS.TO":    {"name": "Fortis Inc.",                       "sector": "Utilities",              "type": "Stock", "exchange": "TSX",    "risk_tier": 1, "priority": 1},
    "EMA.TO":    {"name": "Emera Inc.",                        "sector": "Utilities",              "type": "Stock", "exchange": "TSX",    "risk_tier": 1, "priority": 2},
    "NEE":       {"name": "NextEra Energy",                    "sector": "Utilities",              "type": "Stock", "exchange": "NYSE",   "risk_tier": 1, "priority": 3},
    "D":         {"name": "Dominion Energy",                   "sector": "Utilities",              "type": "Stock", "exchange": "NYSE",   "risk_tier": 1, "priority": 4},

    # ── Real Estate ──────────────────────────────────────────────────────────
    "BAM.TO":    {"name": "Brookfield Asset Management",       "sector": "Financials",             "type": "Stock", "exchange": "TSX",    "risk_tier": 2, "priority": 11},
    "REI-UN.TO": {"name": "RioCan REIT",                       "sector": "Real Estate",            "type": "Stock", "exchange": "TSX",    "risk_tier": 2, "priority": 2},
    "AMT":       {"name": "American Tower",                    "sector": "Real Estate",            "type": "Stock", "exchange": "NYSE",   "risk_tier": 2, "priority": 3},
    "PLD":       {"name": "Prologis",                          "sector": "Real Estate",            "type": "Stock", "exchange": "NYSE",   "risk_tier": 2, "priority": 4},

    # ── Telecommunications ───────────────────────────────────────────────────
    "BCE.TO":    {"name": "BCE Inc.",                          "sector": "Telecommunications",     "type": "Stock", "exchange": "TSX",    "risk_tier": 2, "priority": 1},
    "T.TO":      {"name": "Telus Corporation",                 "sector": "Telecommunications",     "type": "Stock", "exchange": "TSX",    "risk_tier": 1, "priority": 2},
    "VZ":        {"name": "Verizon",                           "sector": "Telecommunications",     "type": "Stock", "exchange": "NYSE",   "risk_tier": 1, "priority": 3},
    "TMUS":      {"name": "T-Mobile US",                       "sector": "Telecommunications",     "type": "Stock", "exchange": "NASDAQ", "risk_tier": 2, "priority": 4},

    # ── Diversified ETF ──────────────────────────────────────────────────────
    "XIU.TO":    {"name": "iShares S&P/TSX 60 ETF",           "sector": "Diversified ETF",        "type": "ETF",   "exchange": "TSX",    "risk_tier": 1, "priority": 1},
    "XIC.TO":    {"name": "iShares Core S&P/TSX Composite ETF","sector": "Diversified ETF",       "type": "ETF",   "exchange": "TSX",    "risk_tier": 1, "priority": 2},
    "SPY":       {"name": "SPDR S&P 500 ETF",                  "sector": "Diversified ETF",        "type": "ETF",   "exchange": "NYSE",   "risk_tier": 1, "priority": 3},
    "QQQ":       {"name": "Invesco QQQ (Nasdaq-100)",          "sector": "Diversified ETF",        "type": "ETF",   "exchange": "NASDAQ", "risk_tier": 2, "priority": 4},
    "VTI":       {"name": "Vanguard Total Stock Market ETF",   "sector": "Diversified ETF",        "type": "ETF",   "exchange": "NYSE",   "risk_tier": 1, "priority": 5},
    "VCN.TO":    {"name": "Vanguard FTSE Canada All Cap ETF",  "sector": "Diversified ETF",        "type": "ETF",   "exchange": "TSX",    "risk_tier": 1, "priority": 6},
    "ZSP.TO":    {"name": "BMO S&P 500 Index ETF",             "sector": "Diversified ETF",        "type": "ETF",   "exchange": "TSX",    "risk_tier": 1, "priority": 7},
    "XEF.TO":    {"name": "iShares Core MSCI EAFE IMI ETF",    "sector": "Diversified ETF",        "type": "ETF",   "exchange": "TSX",    "risk_tier": 2, "priority": 8},
    "IWM":       {"name": "iShares Russell 2000 ETF",          "sector": "Diversified ETF",        "type": "ETF",   "exchange": "NYSE",   "risk_tier": 3, "priority": 9},

    # ── Bond ETF ─────────────────────────────────────────────────────────────
    "ZAG.TO":    {"name": "BMO Aggregate Bond ETF",            "sector": "Bond ETF",               "type": "ETF",   "exchange": "TSX",    "risk_tier": 1, "priority": 1},
    "AGG":       {"name": "iShares US Aggregate Bond ETF",     "sector": "Bond ETF",               "type": "ETF",   "exchange": "NYSE",   "risk_tier": 1, "priority": 2},
    "TLT":       {"name": "iShares 20+ Year Treasury Bond ETF","sector": "Bond ETF",               "type": "ETF",   "exchange": "NASDAQ", "risk_tier": 2, "priority": 3},

    # ── Commodities ETF ──────────────────────────────────────────────────────
    "GLD":       {"name": "SPDR Gold Shares ETF",              "sector": "Commodities ETF",        "type": "ETF",   "exchange": "NYSE",   "risk_tier": 2, "priority": 1},
    "SLV":       {"name": "iShares Silver Trust ETF",          "sector": "Commodities ETF",        "type": "ETF",   "exchange": "NYSE",   "risk_tier": 3, "priority": 2},
}

# Canonical sector display order (ETFs first, then stocks alphabetically)
SECTOR_ORDER = [
    "Diversified ETF",
    "Bond ETF",
    "Commodities ETF",
    "Financials",
    "Technology",
    "Energy",
    "Consumer Staples",
    "Consumer Discretionary",
    "Healthcare",
    "Industrials",
    "Materials",
    "Utilities",
    "Real Estate",
    "Telecommunications",
]

AVAILABLE_SECTORS = sorted({info["sector"] for info in TICKER_POOL.values()})

# Convenience lookups — cover the full pool; import these instead of re-deriving from TICKER_POOL
SECTOR_MAP    = {sym: info["sector"]    for sym, info in TICKER_POOL.items()}
DISPLAY_NAMES = {sym: info["name"]      for sym, info in TICKER_POOL.items()}
RISK_TIER_MAP = {sym: info["risk_tier"] for sym, info in TICKER_POOL.items()}
EXCHANGE_MAP  = {sym: info["exchange"]  for sym, info in TICKER_POOL.items()}
ETF_TICKERS   = {sym for sym, info in TICKER_POOL.items() if info["type"] == "ETF"}

# ---------------------------------------------------------------------------
# Sector risk affinity
#
# Per-sector weight multipliers indexed by user risk band:
#   [0] = conservative (risk 1–3)
#   [1] = moderate     (risk 4–6)
#   [2] = aggressive   (risk 7–10)
#
# Applied to sector pool size when allocating analysis slots, so sectors that
# suit the user's risk profile receive proportionally more tickers.
# ---------------------------------------------------------------------------

SECTOR_RISK_AFFINITY = {
    "Bond ETF":               [2.0, 1.0, 0.3],  # very defensive → aggressive avoids
    "Utilities":              [1.8, 1.0, 0.5],  # stable dividend → aggressive avoids
    "Consumer Staples":       [1.5, 1.0, 0.7],  # defensive → slight penalty at high risk
    "Telecommunications":     [1.4, 1.0, 0.7],  # income-oriented → slight penalty
    "Diversified ETF":        [1.2, 1.2, 1.0],  # always useful, slightly preferred
    "Real Estate":            [1.0, 1.2, 0.8],  # income + moderate growth
    "Healthcare":             [1.0, 1.2, 1.0],  # defensive growth, neutral overall
    "Financials":             [1.0, 1.2, 1.0],  # rate-sensitive, broadly neutral
    "Industrials":            [0.8, 1.2, 1.0],  # cyclical → better for moderate+
    "Consumer Discretionary": [0.7, 1.0, 1.3],  # high-beta consumer → aggressive prefers
    "Energy":                 [0.8, 1.0, 1.2],  # cyclical, volatile → slight aggressive tilt
    "Technology":             [0.7, 1.0, 1.5],  # growth/volatile → aggressive strongly prefers
    "Commodities ETF":        [0.5, 1.0, 1.5],  # speculative → aggressive
    "Materials":              [0.5, 0.8, 1.8],  # most cyclical → aggressive strongly prefers
}


# ---------------------------------------------------------------------------
# Selection helpers
# ---------------------------------------------------------------------------

def _allocate_slots(sector_sizes: dict, total_slots: int) -> dict:
    """
    Distribute total_slots across sectors proportionally to sector_sizes,
    using the Largest Remainder Method to avoid rounding drift.
    """
    if not sector_sizes or total_slots <= 0:
        return {s: 0 for s in sector_sizes}

    total = sum(sector_sizes.values())
    if total == 0:
        return {s: 0 for s in sector_sizes}

    exact = {s: total_slots * n / total for s, n in sector_sizes.items()}
    floors = {s: floor(v) for s, v in exact.items()}
    leftover = total_slots - sum(floors.values())

    # Award remaining slots to sectors with the highest fractional remainder
    ranked = sorted(sector_sizes, key=lambda s: -(exact[s] - floors[s]))
    result = dict(floors)
    for s in ranked[:leftover]:
        result[s] += 1

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def select_tickers(
    size: int = 30,
    user_risk: int = 5,
    sectors: list = None,
    include: list = None,
    exclude: list = None,
) -> list:
    """
    Select `size` tickers from TICKER_POOL using two layers of risk-awareness.

    Layer 1 — Sector allocation:
        Each sector's slot count is proportional to (pool_size × sector_risk_affinity),
        where affinity reflects how well the sector suits the user's risk band.
        Conservative users get more Bond ETF / Utilities slots; aggressive users get
        more Technology / Materials slots.

    Layer 2 — Within-sector ranking:
        Tickers are sorted by (risk_fit, priority), where
        risk_fit = |ticker_risk_tier − user_target_tier|.
        Tickers whose risk tier best matches the user's profile are picked first;
        priority (popularity/liquidity quality) breaks ties.

    Each sector receives at least 1 slot when size >= number of sectors.

    Args:
        size:      Number of tickers to return (default 30; capped at pool size).
        user_risk: User risk level 1–10 (default 5 = moderate). Drives sector
                   weighting and within-sector ranking.
        sectors:   Restrict to these sector names; None means all sectors.
                   Use AVAILABLE_SECTORS to see valid values.
        include:   Force-include these symbols (count toward size).
        exclude:   Force-exclude these symbols.

    Returns:
        Sorted list of ticker symbols.

    Raises:
        ValueError: If user_risk is outside 1–10, or a symbol in `include` is unknown.
    """
    if not 1 <= user_risk <= 10:
        raise ValueError(f"user_risk must be 1–10, got {user_risk}")

    include = list(include or [])
    exclude = set(exclude or [])

    unknown = set(include) - set(TICKER_POOL)
    if unknown:
        raise ValueError(f"Unknown ticker(s) in --include: {', '.join(sorted(unknown))}")

    # Derive target tier and affinity index from user_risk
    target_tier = 1 if user_risk <= 3 else (2 if user_risk <= 6 else 3)
    tier_idx    = target_tier - 1   # 0=conservative, 1=moderate, 2=aggressive

    # Build eligible pool
    pool = {
        sym: info for sym, info in TICKER_POOL.items()
        if sym not in exclude
        and (sectors is None or info["sector"] in sectors)
    }

    forced = [sym for sym in include if sym in pool]
    auto_pool = {sym: info for sym, info in pool.items() if sym not in set(forced)}

    size = min(size, len(pool))
    remaining = max(0, size - len(forced))

    if not auto_pool or remaining == 0:
        return sorted(set(forced))

    # Layer 2: group by sector, sorted by (risk_fit, priority) ascending
    by_sector = defaultdict(list)
    for sym, info in auto_pool.items():
        risk_fit = abs(info["risk_tier"] - target_tier)
        by_sector[info["sector"]].append(((risk_fit, info["priority"]), sym))
    for lst in by_sector.values():
        lst.sort()

    n_sectors = len(by_sector)

    # Layer 1: guarantee ≥1 slot per sector when possible, then distribute leftover
    # using affinity-weighted pool sizes so risk-appropriate sectors get more slots
    base = 1 if remaining >= n_sectors else 0
    leftover = remaining - base * n_sectors

    affinity_weights = {
        s: len(t) * SECTOR_RISK_AFFINITY.get(s, [1.0, 1.0, 1.0])[tier_idx]
        for s, t in by_sector.items()
    }
    extra = _allocate_slots(affinity_weights, leftover)
    sector_slots = {s: base + extra[s] for s in by_sector}

    # Cap each sector at its available tickers; redistribute any surplus
    surplus = 0
    for s in list(by_sector):
        cap = len(by_sector[s])
        if sector_slots[s] > cap:
            surplus += sector_slots[s] - cap
            sector_slots[s] = cap

    if surplus > 0:
        headroom = {s: len(by_sector[s]) - sector_slots[s] for s in by_sector if len(by_sector[s]) > sector_slots[s]}
        extra2 = _allocate_slots(headroom, surplus)
        for s, add in extra2.items():
            sector_slots[s] += add

    # Pick best-fit tickers per sector
    selected = list(forced)
    for sector, tickers in by_sector.items():
        selected.extend(sym for _, sym in tickers[:sector_slots[sector]])

    return sorted(set(selected))


def list_pool(sectors: list = None, symbols: set = None) -> None:
    """
    Print a formatted table of the ticker pool.

    Args:
        sectors: Show only these sectors; None = all.
        symbols: Highlight only these symbols (greys out others); None = show all.
    """
    tier_labels = {1: "Conservative", 2: "Moderate", 3: "Aggressive"}

    by_sector = defaultdict(list)
    for sym, info in TICKER_POOL.items():
        if sectors and info["sector"] not in sectors:
            continue
        by_sector[info["sector"]].append((info["priority"], sym, info))

    # Collect rows in SECTOR_ORDER, filtered to the requested selection
    rows = []
    for sector in SECTOR_ORDER:
        if sector not in by_sector:
            continue
        for _, sym, info in sorted(by_sector[sector]):
            if symbols is not None and sym not in symbols:
                continue
            rows.append((sym, info))

    if not rows:
        print("  (no tickers match)")
        return

    print(f"\n  {'#':<4} {'Symbol':<14} {'Name':<42} {'Sector':<24} {'Exch':<8} Risk Level")
    print(f"  {'-'*4} {'-'*14} {'-'*42} {'-'*24} {'-'*8} {'-'*13}")
    for i, (sym, info) in enumerate(rows, 1):
        print(
            f"  {i:<4} {sym:<14} {info['name']:<42} {info['sector']:<24}"
            f" {info['exchange']:<8} {tier_labels[info['risk_tier']]}"
        )

    n_sectors = len({info["sector"] for _, info in rows})
    print(f"\n  Total: {len(rows)} tickers across {n_sectors} sectors")


# ---------------------------------------------------------------------------
# CLI demo  —  python -m data.ticker_selector [options]
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Preview the ticker universe that would be analysed."
    )
    parser.add_argument("--risk", type=int, default=5, metavar="1-10",
                        help="User risk level 1–10 (default: 5 = moderate)")
    parser.add_argument("--size", type=int, default=30,
                        help="Number of tickers to select (default: 30)")
    parser.add_argument("--sectors", nargs="+", metavar="SECTOR",
                        help=f"Restrict to these sectors. Available: {', '.join(AVAILABLE_SECTORS)}")
    parser.add_argument("--include", nargs="+", metavar="SYMBOL",
                        help="Force-include these ticker symbols")
    parser.add_argument("--exclude", nargs="+", metavar="SYMBOL",
                        help="Force-exclude these ticker symbols")
    parser.add_argument("--list-all", action="store_true",
                        help="List the full pool instead of a selection")
    args = parser.parse_args()

    if args.list_all:
        print(f"\nFull ticker pool ({len(TICKER_POOL)} tickers):")
        list_pool(sectors=args.sectors)
        sys.exit(0)

    try:
        selected = select_tickers(
            size=args.size,
            user_risk=args.risk,
            sectors=args.sectors,
            include=args.include,
            exclude=args.exclude,
        )
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    risk_band = "conservative" if args.risk <= 3 else ("moderate" if args.risk <= 6 else "aggressive")
    sector_label = f", sectors: {', '.join(args.sectors)}" if args.sectors else ""
    print(f"\nSelected {len(selected)} tickers  [risk={args.risk} ({risk_band}){sector_label}]:")
    list_pool(sectors=args.sectors, symbols=set(selected))
