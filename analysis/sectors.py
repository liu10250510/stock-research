"""Sector performance and trend signals for the market summary.

Sector performance is measured with sector ETFs rather than by averaging the
tool's own 86-ticker pool: the pool holds only 2-8 names per sector, which is far
too small a sample to stand in for a sector benchmark.

Both markets are covered because they diverge — Canadian energy and materials
behave quite differently from their US counterparts, and a Canadian investor
holding both needs to see that.

Every signal in this module is **deterministic and descriptive**. Each one states
what has already happened. None of them predict: "energy led all three windows"
is an observation, "energy will continue to lead" is not something this module
will ever emit.

Public API:
    SECTOR_SYMBOLS                       list[str]  — pass to DataFetcher.fetch_all
    build_sectors(raw, windows)          -> {window: [sector rows]}
    compute_trends(sector_windows)       -> {window: {signals}}
"""

import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sector universe
# ---------------------------------------------------------------------------
#
# `style` follows the standard cyclical / defensive / sensitive split. It drives
# the risk-appetite signal: money rotating toward defensives while cyclicals lag
# is the classic risk-off tell, and vice versa.

_SECTORS = [
    # symbol,     sector name,              market,   style
    ("XLK",       "Technology",             "US",     "sensitive"),
    ("XLC",       "Communication Services", "US",     "sensitive"),
    ("XLF",       "Financials",             "US",     "cyclical"),
    ("XLY",       "Consumer Discretionary", "US",     "cyclical"),
    ("XLI",       "Industrials",            "US",     "cyclical"),
    ("XLB",       "Materials",              "US",     "cyclical"),
    ("XLE",       "Energy",                 "US",     "cyclical"),
    ("XLRE",      "Real Estate",            "US",     "cyclical"),
    ("XLV",       "Health Care",            "US",     "defensive"),
    ("XLP",       "Consumer Staples",       "US",     "defensive"),
    ("XLU",       "Utilities",              "US",     "defensive"),

    ("XIT.TO",    "Technology",             "Canada", "sensitive"),
    ("XFN.TO",    "Financials",             "Canada", "cyclical"),
    ("XEG.TO",    "Energy",                 "Canada", "cyclical"),
    ("XMA.TO",    "Materials",              "Canada", "cyclical"),
    ("XBM.TO",    "Base Metals",            "Canada", "cyclical"),
    ("XRE.TO",    "Real Estate",            "Canada", "cyclical"),
    ("ZIN.TO",    "Industrials",            "Canada", "cyclical"),
    ("XST.TO",    "Consumer Staples",       "Canada", "defensive"),
    ("XHC.TO",    "Health Care",            "Canada", "defensive"),
    ("XUT.TO",    "Utilities",              "Canada", "defensive"),
]

SECTOR_SYMBOLS = [row[0] for row in _SECTORS]
_META = {row[0]: {"sector": row[1], "market": row[2], "style": row[3]} for row in _SECTORS}

# A sector must clear this to count as a leader/laggard rather than noise.
_MEANINGFUL = 0.0025          # 0.25%
# Canada vs US gap in the same sector worth calling out.
_DIVERGENCE = 0.02            # 2 percentage points


# ---------------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------------

def build_sectors(raw: dict, windows: dict) -> dict:
    """Return {window_key: [sector row, ...]} sorted best-to-worst.

    Args:
        raw:     DataFetcher.fetch_all output (may include non-sector symbols).
        windows: {window_key: (label, technical return key)}.
    """
    from analysis.technical import compute_technicals

    by_window: dict[str, list] = {k: [] for k in windows}

    for symbol in SECTOR_SYMBOLS:
        data = raw.get(symbol)
        if not data:
            continue
        try:
            hist = data.get("history")
            if hist is None or hist.empty:
                continue
            tech = compute_technicals(hist)
            meta = _META[symbol]
            for wkey, (_, ret_key) in windows.items():
                ret = tech.get(ret_key)
                if ret is None:
                    continue
                by_window[wkey].append({
                    "symbol": symbol,
                    "sector": meta["sector"],
                    "market": meta["market"],
                    "style":  meta["style"],
                    "return": ret,
                })
        except Exception as exc:
            logger.debug("Sector performance failed for %s: %s", symbol, exc)

    for wkey in by_window:
        by_window[wkey].sort(key=lambda r: r["return"], reverse=True)
    return by_window


# ---------------------------------------------------------------------------
# Trend signals
# ---------------------------------------------------------------------------

def compute_trends(sector_windows: dict) -> dict:
    """Derive descriptive trend signals from the per-window sector returns.

    Returns {window_key: {...signals}} plus a "_cross" entry holding signals that
    are only meaningful across windows (persistence, rotation).
    """
    trends: dict[str, dict] = {}

    for wkey, rows in sector_windows.items():
        if not rows:
            trends[wkey] = _empty_window_signals()
            continue

        meaningful = [r for r in rows if abs(r["return"]) >= _MEANINGFUL]
        positive   = [r for r in rows if r["return"] > 0]

        cyc = [r["return"] for r in rows if r["style"] in ("cyclical", "sensitive")]
        dfn = [r["return"] for r in rows if r["style"] == "defensive"]
        spread = (sum(cyc) / len(cyc) - sum(dfn) / len(dfn)) if cyc and dfn else None

        trends[wkey] = {
            "leaders":  [_slim(r) for r in rows[:3] if r["return"] >= _MEANINGFUL],
            "laggards": [_slim(r) for r in reversed(rows[-3:]) if r["return"] <= -_MEANINGFUL],
            "breadth":  round(len(positive) / len(rows), 3),
            "breadth_label": _breadth_label(len(positive) / len(rows)),
            "risk_spread": round(spread, 5) if spread is not None else None,
            "risk_label":  _risk_label(spread),
            "divergences": _divergences(rows),
            "meaningful_count": len(meaningful),
        }

    trends["_cross"] = _cross_window(sector_windows)
    return trends


def _cross_window(sector_windows: dict) -> dict:
    """Persistence and rotation — signals that only exist across time windows."""
    keys = [k for k in ("1d", "1w", "1m") if k in sector_windows and sector_windows[k]]
    if len(keys) < 2:
        return {"sustained_strength": [], "sustained_weakness": [],
                "rotating_in": [], "rotating_out": []}

    # {(sector, market): {window: return}}
    series: dict[tuple, dict] = {}
    for wkey in keys:
        for r in sector_windows[wkey]:
            series.setdefault((r["sector"], r["market"]), {})[wkey] = r["return"]

    sustained_up, sustained_down, rotating_in, rotating_out = [], [], [], []

    for (sector, market), vals in series.items():
        if len(vals) < len(keys):
            continue
        label = {"sector": sector, "market": market}

        if all(v > 0 for v in vals.values()):
            sustained_up.append({**label, "returns": vals})
        elif all(v < 0 for v in vals.values()):
            sustained_down.append({**label, "returns": vals})

        # Rotation: the short window disagrees with the month, by a margin.
        short = vals.get("1w") if "1w" in vals else vals.get("1d")
        month = vals.get("1m")
        if short is not None and month is not None:
            if month <= 0 < short and (short - month) >= _MEANINGFUL * 4:
                rotating_in.append({**label, "short": short, "month": month})
            elif short <= 0 < month and (month - short) >= _MEANINGFUL * 4:
                rotating_out.append({**label, "short": short, "month": month})

    sustained_up.sort(key=lambda d: -sum(d["returns"].values()))
    sustained_down.sort(key=lambda d: sum(d["returns"].values()))

    return {
        "sustained_strength": sustained_up[:4],
        "sustained_weakness": sustained_down[:4],
        "rotating_in":        rotating_in[:4],
        "rotating_out":       rotating_out[:4],
    }


def _divergences(rows: list) -> list:
    """Sectors where Canada and the US moved materially differently."""
    by_sector: dict[str, dict] = {}
    for r in rows:
        by_sector.setdefault(r["sector"], {})[r["market"]] = r["return"]

    out = []
    for sector, markets in by_sector.items():
        ca, us = markets.get("Canada"), markets.get("US")
        if ca is None or us is None:
            continue
        if abs(ca - us) >= _DIVERGENCE:
            out.append({"sector": sector, "canada": ca, "us": us, "gap": round(ca - us, 5)})
    out.sort(key=lambda d: -abs(d["gap"]))
    return out[:3]


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------

def _breadth_label(frac: float) -> str:
    if frac >= 0.75:
        return "Broad advance"
    if frac >= 0.55:
        return "Mostly higher"
    if frac > 0.45:
        return "Mixed"
    if frac > 0.25:
        return "Mostly lower"
    return "Broad decline"


def _risk_label(spread) -> str | None:
    """Cyclicals-minus-defensives. Describes positioning, never predicts it."""
    if spread is None:
        return None
    if spread >= 0.01:
        return "Risk-on — cyclicals leading defensives"
    if spread <= -0.01:
        return "Risk-off — defensives leading cyclicals"
    return "Neutral — no clear cyclical/defensive tilt"


def _slim(row: dict) -> dict:
    return {"sector": row["sector"], "market": row["market"], "return": row["return"]}


def _empty_window_signals() -> dict:
    return {"leaders": [], "laggards": [], "breadth": None, "breadth_label": None,
            "risk_spread": None, "risk_label": None, "divergences": [],
            "meaningful_count": 0}
