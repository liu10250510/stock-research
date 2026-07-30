"""
PDF Report Generator (Step 11, §16).

Produces a 12-page PDF investment research report using ReportLab Platypus.

Public API:
    generate_pdf(output_path, picks, macro_context, scenario_result,
                 ticker_data, risk_scores, risk_metrics,
                 user_risk, amount=50_000, platform="questrade") -> None
"""

import io
import logging
from datetime import date

from reportlab.lib.colors import HexColor, white, black
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable, Image, KeepTogether, PageBreak,
    Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from report.charts import (
    correlation_heatmap, dividend_history_chart, forecast_chart,
    portfolio_allocation_pie, price_chart, risk_score_breakdown,
    rsi_chart, scenario_bar_chart, sector_pie_chart, sentiment_gauge,
    universe_risk_bar,
)
from data.ticker_selector import EXCHANGE_MAP
from analysis.risk_metrics import RISK_FREE_RATE

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------

PAGE_W, PAGE_H = letter          # 612 × 792 pt
MARGIN    = 0.75 * inch          # 54 pt
CONTENT_W = PAGE_W - 2 * MARGIN  # 504 pt

# Chart heights derived from fixed aspect ratios
_PRICE_H   = CONTENT_W * 4 / 10   # 10:4 → 201.6 pt
_WIDE_H    = CONTENT_W * 3 / 8    # 8:3  → 189 pt
_HALF_W    = (CONTENT_W - 6) / 2  # ≈249 pt (side-by-side with 6pt gap)
_HALF_H    = _HALF_W * 3 / 8      # 8:3 at half width

# ---------------------------------------------------------------------------
# Color palette  §16.1
# ---------------------------------------------------------------------------

NAVY   = HexColor("#1B3A6B")
BLUE   = HexColor("#2E86AB")
ORANGE = HexColor("#F18F01")
GREEN  = HexColor("#28A745")
RED    = HexColor("#DC3545")
AMBER  = HexColor("#FFC107")
LGRAY  = HexColor("#F5F5F5")
DTEXT  = HexColor("#333333")

# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------

_BASE = getSampleStyleSheet()

def _make_styles():
    return {
        "cover_title": ParagraphStyle(
            "cover_title", parent=_BASE["Title"],
            fontSize=28, textColor=NAVY, spaceAfter=12, alignment=TA_CENTER,
        ),
        "cover_sub": ParagraphStyle(
            "cover_sub", parent=_BASE["Normal"],
            fontSize=11, textColor=DTEXT, spaceAfter=6, alignment=TA_CENTER,
        ),
        "section": ParagraphStyle(
            "section", parent=_BASE["Normal"],
            fontSize=12, textColor=white, backColor=NAVY,
            spaceAfter=8, spaceBefore=12,
            leftIndent=6, rightIndent=6, leading=18,
        ),
        "subsection": ParagraphStyle(
            "subsection", parent=_BASE["Normal"],
            fontSize=10, textColor=NAVY, spaceAfter=4, spaceBefore=8,
            fontName="Helvetica-Bold",
        ),
        "body": ParagraphStyle(
            "body", parent=_BASE["Normal"],
            fontSize=9, textColor=DTEXT, spaceAfter=4, leading=13,
        ),
        "small": ParagraphStyle(
            "small", parent=_BASE["Normal"],
            fontSize=7.5, textColor=HexColor("#777777"), spaceAfter=3, leading=11,
        ),
        "disclaimer": ParagraphStyle(
            "disclaimer", parent=_BASE["Normal"],
            fontSize=8, textColor=HexColor("#888888"),
            alignment=TA_CENTER, spaceAfter=4,
        ),
        "bullet": ParagraphStyle(
            "bullet", parent=_BASE["Normal"],
            fontSize=9, textColor=DTEXT, spaceAfter=3,
            leftIndent=12, bulletIndent=0, leading=13,
        ),
        "warning": ParagraphStyle(
            "warning", parent=_BASE["Normal"],
            fontSize=9, textColor=HexColor("#7B3F00"), spaceAfter=3,
            leftIndent=12, leading=13,
        ),
        "th": ParagraphStyle(
            "th", parent=_BASE["Normal"],
            fontSize=8, textColor=white, fontName="Helvetica-Bold",
        ),
        "td": ParagraphStyle(
            "td", parent=_BASE["Normal"],
            fontSize=8, textColor=DTEXT,
        ),
        "td_small": ParagraphStyle(
            "td_small", parent=_BASE["Normal"],
            fontSize=7, textColor=DTEXT,
        ),
    }

ST = _make_styles()

# ---------------------------------------------------------------------------
# Flowable helpers
# ---------------------------------------------------------------------------

def _sp(n=6):
    return Spacer(1, n)

def _hr():
    return HRFlowable(width="100%", thickness=0.5, color=HexColor("#CCCCCC"), spaceAfter=6)

def _section(title: str):
    return Paragraph(f"<b>{title}</b>", ST["section"])

def _subsection(title: str):
    return Paragraph(f"<b>{title}</b>", ST["subsection"])

def _body(text: str):
    return Paragraph(text or "—", ST["body"])

def _small(text: str):
    return Paragraph(text or "", ST["small"])

def _img(buf: io.BytesIO, width: float, height: float) -> Image:
    buf.seek(0)
    return Image(buf, width=width, height=height)

# Tallest image we can place and still leave room for a heading on the page.
_MAX_IMG_H = PAGE_H - 2 * MARGIN - 60

# Table styles are immutable and Table.setStyle only reads from them, so one
# instance each is shared across the ~60 tables in a report rather than rebuilt.
_KV_STYLE = TableStyle([
    ("BACKGROUND",    (0, 0), (0, -1), LGRAY),
    ("ROWBACKGROUNDS",(0, 0), (-1, -1), [white, LGRAY]),
    ("GRID",          (0, 0), (-1, -1), 0.3, HexColor("#DDDDDD")),
    ("TOPPADDING",    (0, 0), (-1, -1), 4),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ("LEFTPADDING",   (0, 0), (-1, -1), 6),
    ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
])

_DATA_STYLE = TableStyle([
    ("BACKGROUND",    (0, 0), (-1, 0), NAVY),
    ("ROWBACKGROUNDS",(0, 1), (-1, -1), [white, LGRAY]),
    ("GRID",          (0, 0), (-1, -1), 0.3, HexColor("#DDDDDD")),
    ("TOPPADDING",    (0, 0), (-1, -1), 4),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ("LEFTPADDING",   (0, 0), (-1, -1), 5),
    ("RIGHTPADDING",  (0, 0), (-1, -1), 5),
    ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
])

# Two of these are built per ticker page.
_MINI_STYLE = TableStyle([
    ("BACKGROUND",    (0, 0), (-1, 0),   BLUE),
    ("TEXTCOLOR",     (0, 0), (-1, 0),   white),
    ("FONTNAME",      (0, 0), (-1, 0),   "Helvetica-Bold"),
    ("FONTSIZE",      (0, 0), (-1, -1),  8),
    ("ROWBACKGROUNDS",(0, 1), (-1, -1),  [white, LGRAY]),
    ("GRID",          (0, 0), (-1, -1),  0.3, HexColor("#DDDDDD")),
    ("TOPPADDING",    (0, 0), (-1, -1),  4),
    ("BOTTOMPADDING", (0, 0), (-1, -1),  4),
    ("LEFTPADDING",   (0, 0), (-1, -1),  6),
])

_SIDE_BY_SIDE_STYLE = TableStyle([
    ("LEFTPADDING",  (0, 0), (-1, -1), 0),
    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
])

def _pct(v, decimals=1):
    if v is None:
        return "—"
    return f"{v:.{decimals}%}"

def _num(v, fmt=".2f"):
    if v is None:
        return "—"
    try:
        return f"{v:{fmt}}"
    except Exception:
        return str(v)

def _risk_color(score):
    if score is None:
        return DTEXT
    if score <= 3.5:
        return GREEN
    if score <= 6.5:
        return AMBER
    return RED

def _trunc(text: str, max_words: int) -> str:
    if not text:
        return ""
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + "…"

def _kv_table(pairs: list, col_widths=None) -> Table:
    """Two-column key-value table. pairs = [(label, value), ...]"""
    col_widths = col_widths or [CONTENT_W * 0.38, CONTENT_W * 0.62]
    data = [[Paragraph(f"<b>{k}</b>", ST["td"]),
             Paragraph(str(v) if v is not None else "—", ST["td"])]
            for k, v in pairs]
    t = Table(data, colWidths=col_widths, hAlign="LEFT")
    t.setStyle(_KV_STYLE)
    return t

def _data_table(headers: list, rows: list, col_widths=None) -> Table:
    """Full data table with header row."""
    header_row = [Paragraph(h, ST["th"]) for h in headers]
    body_rows  = [
        [Paragraph(str(c) if c is not None else "—", ST["td_small"]) for c in row]
        for row in rows
    ]
    data = [header_row] + body_rows
    n_cols = len(headers)
    col_widths = col_widths or [CONTENT_W / n_cols] * n_cols
    t = Table(data, colWidths=col_widths, hAlign="LEFT", repeatRows=1)
    t.setStyle(_DATA_STYLE)
    return t

# ---------------------------------------------------------------------------
# Risk band helpers
# ---------------------------------------------------------------------------

_RISK_DESCRIPTIONS = {
    (1, 3): (
        "Conservative",
        "Capital preservation with modest income. Focus on low-volatility, dividend-paying "
        "equities, bond ETFs, and defensive sectors (Utilities, Consumer Staples, Healthcare). "
        "Accepts lower long-term returns in exchange for reduced drawdowns.",
    ),
    (4, 6): (
        "Moderate",
        "Balanced growth with managed downside. Mix of growth equities, dividend stocks, "
        "diversified ETFs, and some fixed income. Tolerates moderate short-term swings "
        "for above-inflation long-term returns.",
    ),
    (7, 10): (
        "Aggressive",
        "Maximum growth potential. Concentrated in high-growth sectors (Technology, Materials, "
        "Consumer Discretionary). Accepts large short-term drawdowns for potentially superior "
        "long-term compounding. Not suitable for short investment horizons.",
    ),
}

def _risk_band_info(user_risk: int):
    for (lo, hi), (label, desc) in _RISK_DESCRIPTIONS.items():
        if lo <= user_risk <= hi:
            return label, desc
    return "Moderate", _RISK_DESCRIPTIONS[(4, 6)][1]

# ---------------------------------------------------------------------------
# Page 1 — Cover
# ---------------------------------------------------------------------------

def _page_cover(story: list, user_risk: int):
    story.append(_sp(60))
    story.append(Paragraph("Stock Market Research Report", ST["cover_title"]))
    story.append(_sp(8))
    story.append(_hr())
    story.append(_sp(20))

    # Risk badge
    risk_label, _ = _risk_band_info(user_risk)
    badge_color = GREEN if user_risk <= 3 else (AMBER if user_risk <= 6 else RED)
    badge_data = [[
        Paragraph(f"<b>Risk Level {user_risk} / 10 — {risk_label}</b>",
                  ParagraphStyle("badge", parent=_BASE["Normal"],
                                 fontSize=14, textColor=white, alignment=TA_CENTER))
    ]]
    badge = Table(badge_data, colWidths=[CONTENT_W * 0.6], hAlign="CENTER")
    badge.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), badge_color),
        ("TOPPADDING",    (0, 0), (-1, -1), 14),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
        ("LEFTPADDING",   (0, 0), (-1, -1), 20),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 20),
        ("ROUNDEDCORNERS",[4]),
    ]))
    story.append(badge)
    story.append(_sp(30))

    today = date.today().strftime("%B %d, %Y")
    story.append(Paragraph(f"Generated: {today}", ST["cover_sub"]))
    story.append(_sp(6))
    story.append(Paragraph(
        "Data source: Yahoo Finance (prices delayed ~15 min) · Web research: DuckDuckGo",
        ST["cover_sub"],
    ))
    story.append(_sp(14))
    story.append(Paragraph(
        f"Key assumptions: risk-free rate {RISK_FREE_RATE:.2%} (applied to both CAD and "
        "USD assets); Sharpe and Sortino use trailing 1-year returns and volatility. "
        "Figures for each security are stated in its own listing currency — CAD for "
        "TSX-listed, USD for US-listed — and are not converted. A Canadian holder's "
        "realized return on USD positions will differ by the CAD/USD move over the period.",
        ST["small"],
    ))
    story.append(_sp(60))
    story.append(Paragraph(
        "⚠ Not financial advice. For educational purposes only. "
        "Past performance does not guarantee future results. "
        "Always consult a licensed financial adviser before investing.",
        ST["disclaimer"],
    ))
    story.append(PageBreak())

# ---------------------------------------------------------------------------
# Page 2 — Macro & Market Context
# ---------------------------------------------------------------------------

def _page_macro(story: list, macro_context: dict, user_risk: int):
    story.append(_section("Macro & Market Context"))
    story.append(_sp(6))

    rate   = macro_context.get("rate_environment", "stable").capitalize()
    inf    = macro_context.get("inflation_level",  "moderate").capitalize()
    mom    = macro_context.get("economic_momentum","expanding").capitalize()
    desc   = macro_context.get("regime_description", "")
    unavail = macro_context.get("data_unavailable", False)

    if unavail:
        story.append(_body("⚠ Live macro data unavailable — analysis uses neutral defaults."))
        story.append(_sp(4))

    story.append(_kv_table([
        ("Rate environment",  rate),
        ("Inflation level",   inf),
        ("Economic momentum", mom),
    ]))
    story.append(_sp(8))
    story.append(_body(desc))
    story.append(_sp(10))

    # Sector modifiers table
    story.append(_subsection("Sector Impact for This Portfolio"))
    mods = macro_context.get("sector_modifiers", {})
    if mods:
        sorted_mods = sorted(mods.items(), key=lambda x: x[1], reverse=True)
        rows = []
        for sector, mod in sorted_mods:
            direction = ("↑ Favoured" if mod > 0.1 else
                         ("↓ Headwind" if mod < -0.1 else "→ Neutral"))
            color_tag = ("green" if mod > 0.1 else ("red" if mod < -0.1 else "gray"))
            rows.append([sector, f"{mod:+.2f}", direction])
        story.append(_data_table(
            ["Sector", "Modifier", "Outlook"],
            rows,
            col_widths=[CONTENT_W * 0.45, CONTENT_W * 0.2, CONTENT_W * 0.35],
        ))
    story.append(PageBreak())

# ---------------------------------------------------------------------------
# Page 3 — Risk Profile Summary
# ---------------------------------------------------------------------------

def _page_risk_profile(story: list, user_risk: int, all_scores: dict,
                       risk_bar_png: bytes | None = None):
    story.append(_section("Risk Profile Summary"))
    story.append(_sp(6))

    risk_label, risk_desc = _risk_band_info(user_risk)
    story.append(_subsection(f"Level {user_risk} — {risk_label}"))
    story.append(_body(risk_desc))
    story.append(_sp(8))

    story.append(_subsection("Investment Philosophy"))
    philosophy = {
        (1, 3): (
            "Prioritise income and capital preservation. Rebalance annually. "
            "Hold ≥3 CAD-listed positions for currency stability. "
            "Target portfolio beta below 0.7."
        ),
        (4, 6): (
            "Balance growth with risk management. Diversify across sectors and currencies. "
            "Target portfolio beta 0.7–1.1. Rebalance semi-annually."
        ),
        (7, 10): (
            "Accept volatility for long-term outperformance. Concentrate in high-conviction sectors. "
            "Target portfolio beta above 1.0. Review quarterly; momentum-follow strong performers."
        ),
    }
    for (lo, hi), text in philosophy.items():
        if lo <= user_risk <= hi:
            story.append(_body(text))
            break

    story.append(_sp(10))
    story.append(_subsection("Platform Cost Notes"))
    story.append(_body(
        "Questrade: ETF buys free; stock trades $4.95–$9.95. "
        "Wealthsimple: commission-free (CAD); USD trades $2/trade. "
        "TD Direct: $9.99/trade flat. "
        "All platforms: USD positions incur ~1.5% FX conversion spread."
    ))
    story.append(_sp(10))

    # Universe risk bar — rendered once in generate_pdf, shared with page 12
    if risk_bar_png:
        story.append(_subsection("Universe Risk Score Distribution"))
        story.append(_img(io.BytesIO(risk_bar_png), CONTENT_W, _WIDE_H))
    story.append(PageBreak())

# ---------------------------------------------------------------------------
# Page 4 — Top Recommendations Table
# ---------------------------------------------------------------------------

def _page_recommendations(story: list, picks: list):
    story.append(_section("Top Recommendations"))
    story.append(_sp(6))

    cw = [
        38, 90, 32, 72, 40, 38, 44, 56, 48, 46, 40,
    ]  # ~504 total
    headers = [
        "Symbol", "Name", "Type", "Sector",
        "Risk", "Sharpe", "1y Ret", "Consensus",
        "Sentiment", "3m proj.", "Wt%",
    ]
    rows = []
    for p in picks:
        f    = p.get("fundamentals", {}) or {}
        t    = p.get("technicals",   {}) or {}
        fc   = p.get("forecast",     {}) or {}
        s    = p.get("sentiment",    {}) or {}
        name = (f.get("name") or p["symbol"])[:18]
        typ  = "ETF" if p.get("is_etf") else "Stock"
        sector = (f.get("sector") or "—")[:12]
        rows.append([
            p["symbol"],
            name,
            typ,
            sector,
            _num(p.get("risk_score"), ".1f"),
            _num(p.get("sharpe_ratio"), ".2f"),
            _pct(t.get("return_1y")),
            (fc.get("consensus_label") or "—")[:10],
            s.get("sentiment_label", "—")[:8],
            _pct(fc.get("upside_pct") / 100 if fc.get("upside_pct") is not None else None),
            f"{p.get('weight', 0)}%",
        ])
    story.append(_data_table(headers, rows, col_widths=cw))
    story.append(_sp(8))
    story.append(_small(
        "Risk: 1–10 (lower = more conservative). Sharpe: return per unit of risk (higher = better). "
        "1y Ret: total return in the security's own listing currency. "
        "3m proj.: model estimate blending analyst targets with 3-month price momentum — "
        "not an analyst target. For ETFs, which have no analyst coverage, it is momentum only. "
        "Wt%: recommended portfolio weight."
    ))
    story.append(PageBreak())

# ---------------------------------------------------------------------------
# Page 5 — Portfolio Allocation & Diversification
# ---------------------------------------------------------------------------

def _page_allocation(story: list, picks: list, rec_result: dict, ticker_data: dict):
    story.append(_section("Portfolio Allocation & Diversification"))
    story.append(_sp(6))

    # Allocation pie chart
    try:
        buf = portfolio_allocation_pie(picks)
        story.append(_img(buf, CONTENT_W, _WIDE_H))
    except Exception as exc:
        logger.warning("portfolio_allocation_pie failed: %s", exc)
    story.append(_sp(8))

    # Allocation table
    story.append(_subsection("Allocation Detail"))
    headers = ["Symbol", "Name", "Weight", "Sector", "Currency", "Risk Score"]
    rows = []
    for p in picks:
        f = p.get("fundamentals", {}) or {}
        # Resolved from the fetched listing data; falls back to the static map.
        currency = p.get("currency") or (
            "CAD" if EXCHANGE_MAP.get(p["symbol"], "NYSE") == "TSX" else "USD")
        rows.append([
            p["symbol"],
            (f.get("name") or p["symbol"])[:20],
            f"{p.get('weight', 0)}%",
            (f.get("sector") or "—")[:16],
            currency,
            _num(p.get("risk_score"), ".1f"),
        ])
    story.append(_data_table(headers, rows,
        col_widths=[48, 120, 42, 100, 54, 54]))
    story.append(_sp(8))

    # Sector & currency breakdown
    sector_bd   = rec_result.get("sector_breakdown",   {})
    currency_bd = rec_result.get("currency_breakdown", {})
    avg_corr    = rec_result.get("avg_portfolio_corr")

    pairs = [
        ("Sector breakdown", ", ".join(f"{s}: {n}" for s, n in sector_bd.items())),
        ("Currency",         f"CAD: {currency_bd.get('CAD',0)}  |  USD: {currency_bd.get('USD',0)}"),
        ("Avg pair correlation", f"{avg_corr:.3f}" if avg_corr is not None else "n/a"),
    ]
    story.append(_kv_table(pairs))
    story.append(_sp(10))

    # Correlation heatmap — matrix comes from recommend(); no recompute here.
    try:
        corr = rec_result.get("correlation_matrix")
        if corr is not None and not corr.empty:
            syms = list(corr.columns)
            story.append(_subsection("Pick Correlation Heatmap"))
            buf = correlation_heatmap(corr, syms)
            sz  = min(CONTENT_W, len(syms) * 52)
            story.append(_img(buf, sz, sz * 0.85))
            story.append(_sp(8))
    except Exception as exc:
        logger.warning("correlation_heatmap failed: %s", exc)

    # Portfolio cost summary
    pc = rec_result.get("portfolio_cost", {})
    if pc:
        story.append(_subsection("Portfolio Cost Summary"))

        t_cad = pc.get("trading_cost_cad", 0) or 0
        t_usd = pc.get("trading_cost_usd", 0) or 0
        commissions = f"${t_cad:.2f} CAD"
        if t_usd:
            # Kept separate — these are quoted in the listing currency and were
            # previously summed into a single "CAD" figure.
            commissions += f"  +  ${t_usd:.2f} USD"

        drag_cad = pc.get("annual_etf_drag_cad")
        drag_pct = pc.get("annual_etf_drag_pct")
        drag_txt = (f"${drag_cad:.2f}  ({drag_pct:.3f}%)"
                    if drag_cad is not None and drag_pct is not None
                    else "Not available")

        cost_pairs = [
            ("Assumed portfolio size",   f"${pc.get('assumed_portfolio_cad',0):,} CAD"),
            ("One-time commissions",     commissions),
            ("FX conversion (USD buys)", f"${pc.get('total_fx_conversion_cost',0):.2f} CAD"),
            ("Annual ETF drag",          drag_txt),
        ]
        if pc.get("etf_cost_efficiency"):
            cost_pairs.append(("ETF cost efficiency", pc["etf_cost_efficiency"]))
        cost_pairs.append(("Platform", pc.get('platform', '—').replace('_', ' ').title()))

        story.append(_kv_table(cost_pairs))
        breakdown = pc.get("cost_efficiency_breakdown", [])
        if breakdown:
            story.append(_sp(4))
            story.append(_data_table(
                ["ETF", "MER %", "Annual drag on position"],
                [[b["symbol"], b["mer_pct"], f"${b['annual_drag_on_position']:.2f}"]
                 for b in breakdown],
                col_widths=[80, 80, CONTENT_W - 160],
            ))
        missing = pc.get("etfs_missing_mer") or []
        if missing:
            story.append(_small(
                "MER unavailable from the data source for: " + ", ".join(missing) +
                ". Annual drag above excludes them and is therefore understated."))
        story.append(_small(pc.get("note", "")))
    story.append(PageBreak())

# ---------------------------------------------------------------------------
# Page 6 — Portfolio Scenario Analysis
# ---------------------------------------------------------------------------

def _page_scenarios(story: list, scenario_result: dict):
    story.append(_section("Portfolio Scenario Analysis"))
    story.append(_sp(6))

    scenarios = scenario_result.get("scenarios", {})

    # Scenario bar chart
    try:
        buf = scenario_bar_chart(scenarios)
        story.append(_img(buf, CONTENT_W, _WIDE_H))
    except Exception as exc:
        logger.warning("scenario_bar_chart failed: %s", exc)
    story.append(_sp(8))

    # Summary table
    headers = ["Scenario", "Portfolio Return", "Best Pick", "Worst Pick"]
    rows = []
    _labels = {
        "crash":     "Market Crash (−25%)",
        "rate_hike": "Rate Hike (+1%)",
        "bull_run":  "Bull Run (+20%)",
        "recession": "Recession",
    }
    for key, label in _labels.items():
        sc = scenarios.get(key, {})
        bp = sc.get("best_pick",  {})
        wp = sc.get("worst_pick", {})
        rows.append([
            label,
            _pct(sc.get("portfolio_return")),
            f"{bp.get('symbol','—')} ({_pct(bp.get('return'))})" if bp else "—",
            f"{wp.get('symbol','—')} ({_pct(wp.get('return'))})" if wp else "—",
        ])
    story.append(_data_table(headers, rows,
        col_widths=[CONTENT_W*0.28, CONTENT_W*0.18, CONTENT_W*0.27, CONTENT_W*0.27]))
    story.append(_sp(8))

    # Portfolio-level stats
    story.append(_subsection("Portfolio Statistics"))
    story.append(_kv_table([
        ("Portfolio beta",       _num(scenario_result.get("portfolio_beta"), ".2f")),
        ("Weighted avg Sharpe",  _num(scenario_result.get("portfolio_sharpe"), ".2f")),
        ("Weighted avg max drawdown", _pct(scenario_result.get("portfolio_max_dd"))),
    ]))
    story.append(_small(
        "Sharpe and max drawdown are weight-averaged across holdings, not computed on the "
        "combined portfolio. Neither is linear in weights: the averaged Sharpe ignores the "
        "diversification benefit shown by the correlation figures, and the averaged drawdown "
        "assumes every holding bottoms on the same day, so it is a pessimistic bound."
    ))
    story.append(_sp(8))

    # Plain-English interpretations
    story.append(_subsection("Scenario Interpretations"))
    interps = [
        ("Market Crash", "Beta-driven losses. Lower-beta picks (bonds, utilities, staples) provide "
         "the most protection. The worst-case pick shows maximum theoretical loss under a 2008-style event."),
        ("Rate Hike",    "Financials benefit from wider spreads; bond ETFs, real estate, and utilities "
         "face headwinds. Rate-sensitive sectors should be monitored if the Bank of Canada signals tightening."),
        ("Bull Run",     "High-beta technology and growth picks lead the upside. "
         "Conservative picks lag but still participate. This scenario rewards risk-taking."),
        ("Recession",    "Defensive sectors (Consumer Staples, Healthcare, Utilities) outperform. "
         "Cyclical sectors (Energy, Financials, Discretionary) face the steepest declines."),
    ]
    for title, text in interps:
        story.append(_body(f"<b>{title}:</b> {text}"))
    story.append(PageBreak())

# ---------------------------------------------------------------------------
# Pages 7–11 — Individual Ticker Pages
# ---------------------------------------------------------------------------

def _page_ticker(story: list, pick: dict, ticker_data: dict):
    sym  = pick["symbol"]
    f    = pick.get("fundamentals", {}) or {}
    t    = pick.get("technicals",   {}) or {}
    fc   = pick.get("forecast",     {}) or {}
    s    = pick.get("sentiment",    {}) or {}
    cons = pick.get("consensus",    {}) or {}
    expl = pick.get("explanation",  {}) or {}
    rm   = {
        "sharpe_ratio":  pick.get("sharpe_ratio"),
        "sortino_ratio": pick.get("sortino_ratio"),
        "max_drawdown":  pick.get("max_drawdown"),
        "annualized_vol":pick.get("annualized_vol"),
    }
    cost    = f.get("cost", {}) or {}
    is_etf  = pick.get("is_etf", False)
    name    = f.get("name") or sym
    sector  = f.get("sector") or (f.get("description") or "ETF")[:40] if is_etf else (f.get("sector") or "—")
    risk_sc = pick.get("risk_score")

    # Raw history for charts
    raw_td  = ticker_data.get(sym, {})
    history = raw_td.get("history") if raw_td else None

    # ── Header ──────────────────────────────────────────────────────────────
    badge_color = _risk_color(risk_sc)
    hdr_data = [[
        Paragraph(f"<b>{name}</b>", ParagraphStyle(
            "hdr", parent=_BASE["Normal"], fontSize=13, textColor=NAVY)),
        Paragraph(
            f"<b>{sym}</b>  |  {'ETF' if is_etf else 'Stock'}  |  {sector}",
            ParagraphStyle("hdr2", parent=_BASE["Normal"], fontSize=9, textColor=DTEXT)),
        Paragraph(
            f"<b>Risk {_num(risk_sc,'.1f')}</b>",
            ParagraphStyle("badge2", parent=_BASE["Normal"], fontSize=11,
                           textColor=white, alignment=TA_CENTER)),
    ]]
    hdr_table = Table(hdr_data, colWidths=[CONTENT_W*0.45, CONTENT_W*0.38, CONTENT_W*0.17])
    hdr_table.setStyle(TableStyle([
        ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND",  (2, 0), (2, 0),   badge_color),
        ("BACKGROUND",  (0, 0), (1, 0),   LGRAY),
        ("TOPPADDING",  (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING",(0,0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(hdr_table)
    story.append(_sp(8))

    # ── Price + RSI charts ───────────────────────────────────────────────────
    if history is not None and not history.empty:
        try:
            p_buf = price_chart(history, sym)
            story.append(_img(p_buf, CONTENT_W, _PRICE_H))
        except Exception as exc:
            logger.warning("price_chart(%s): %s", sym, exc)
        story.append(_sp(4))
        try:
            r_buf = rsi_chart(history, sym)
            story.append(_img(r_buf, CONTENT_W, _WIDE_H * 0.75))
        except Exception as exc:
            logger.warning("rsi_chart(%s): %s", sym, exc)
        story.append(_sp(8))

    # ── Forecast chart ───────────────────────────────────────────────────────
    current_price = t.get("current_price") or fc.get("current_price")
    if current_price and fc.get("forecast_3m"):
        try:
            f_buf = forecast_chart(current_price, fc, sym)
            story.append(_img(f_buf, CONTENT_W, _WIDE_H))
        except Exception as exc:
            logger.warning("forecast_chart(%s): %s", sym, exc)
        # State what the projection is actually built from — it is not an
        # analyst target, and for ETFs it contains no analyst input at all.
        _basis_note = {
            "blend":    "3-month projection blends analyst price targets with price momentum.",
            "analyst":  "3-month projection derived from analyst price targets.",
            "momentum": "3-month projection derived from price momentum only — "
                        "this security has no analyst price target.",
        }.get(fc.get("forecast_basis"))
        if _basis_note:
            au = fc.get("analyst_upside_pct")
            if au is not None:
                _basis_note += (f" Analyst mean target separately implies "
                                f"{au:+.1f}% over 12 months.")
            story.append(_small(_basis_note))
        story.append(_sp(8))

    # ── Returns + risk metrics ───────────────────────────────────────────────
    story.append(_subsection("Returns & Risk-Adjusted Metrics"))
    ret_rows = [["Period", "Total Return (incl. dividends)"],
                ["1 Month", _pct(t.get("return_1m"))],
                ["3 Month", _pct(t.get("return_3m"))], ["1 Year", _pct(t.get("return_1y"))],
                ["3 Year",  _pct(t.get("return_3y"))], ["5 Year", _pct(t.get("return_5y"))]]
    met_rows = [["Metric", "Value"],
                ["Sharpe Ratio",   _num(rm["sharpe_ratio"], ".2f")],
                ["Sortino Ratio",  _num(rm["sortino_ratio"], ".2f")],
                ["Max Drawdown",   _pct(rm["max_drawdown"])],
                ["Annualized Vol", _pct(rm["annualized_vol"])]]

    def _mini_table(data):
        t_obj = Table(data, colWidths=[_HALF_W*0.5, _HALF_W*0.5])
        t_obj.setStyle(_MINI_STYLE)
        return t_obj

    side_by_side = Table(
        [[_mini_table(ret_rows), _mini_table(met_rows)]],
        colWidths=[_HALF_W, _HALF_W], hAlign="LEFT",
    )
    side_by_side.setStyle(_SIDE_BY_SIDE_STYLE)
    story.append(side_by_side)
    story.append(_sp(8))

    # ── Risk score breakdown chart ───────────────────────────────────────────
    components = pick.get("risk_score_components") or {}
    if components:
        try:
            rb_buf = risk_score_breakdown(components, sym)
            story.append(_img(rb_buf, CONTENT_W, _WIDE_H))
            story.append(_sp(8))
        except Exception as exc:
            logger.warning("risk_score_breakdown(%s): %s", sym, exc)

    # ── Trading activity ────────────────────────────────────────────────────
    story.append(_kv_table([
        ("30d volume",         f"{t.get('vol_30d_avg') or 0:,} avg daily / {t.get('vol_30d_total') or 0:,} total"),
        ("90d avg daily vol",  f"{t.get('vol_90d_avg') or 0:,}"),
        ("Activity",           t.get("vol_activity") or "—"),
    ]))
    story.append(_sp(8))

    if is_etf:
        _etf_section(story, pick, f)
    else:
        _stock_section(story, pick, f)

    # ── Analyst consensus ────────────────────────────────────────────────────
    story.append(_subsection("Analyst Consensus"))
    total = (cons.get("buy_count",0) or 0) + (cons.get("hold_count",0) or 0) + (cons.get("sell_count",0) or 0)
    story.append(_kv_table([
        ("Consensus label",   cons.get("consensus_label", "—")),
        ("Buy / Hold / Sell", f"{cons.get('buy_count',0)} / {cons.get('hold_count',0)} / {cons.get('sell_count',0)}  (of {total} analysts)"),
        ("Recent upgrades",   str(cons.get("recent_upgrades", 0))),
        ("Recent downgrades", str(cons.get("recent_downgrades", 0))),
    ]))
    story.append(_sp(8))

    # ── Sentiment gauge ──────────────────────────────────────────────────────
    story.append(_subsection("Web Sentiment"))
    sent_score = s.get("sentiment_score", 0.0)
    try:
        sg_buf = sentiment_gauge(sent_score, sym)
        story.append(_img(sg_buf, min(CONTENT_W, 360), _WIDE_H * 0.85))
    except Exception as exc:
        logger.warning("sentiment_gauge(%s): %s", sym, exc)

    risk_factors = s.get("risk_factors", []) or []
    if risk_factors:
        story.append(_sp(4))
        story.append(_subsection("Identified Risk Factors"))
        for rf in risk_factors:
            story.append(_body(f"• <b>{rf.get('category','?')}:</b> {rf.get('evidence','')}"))
    story.append(_sp(8))

    # ── Explainable reasoning ────────────────────────────────────────────────
    story.append(_subsection("Why This Pick"))
    for b in expl.get("why_bullets", []):
        story.append(Paragraph(f"✓  {b}", ST["bullet"]))
    if expl.get("watch_out"):
        story.append(_sp(4))
        story.append(_subsection("Watch Out For"))
        for w in expl.get("watch_out", []):
            story.append(Paragraph(f"⚠  {w}", ST["warning"]))
    story.append(_sp(8))

    # ── Business / description summary ──────────────────────────────────────
    summary = f.get("business_summary") or f.get("description")
    if summary:
        story.append(_subsection("About"))
        story.append(_body(_trunc(summary, 120)))
    story.append(_sp(6))

    # ── Web research highlights ──────────────────────────────────────────────
    raw_summary = s.get("raw_summary")
    if raw_summary:
        story.append(_subsection("Web Research Highlights"))
        story.append(_body(_trunc(raw_summary, 150)))
    story.append(PageBreak())


def _stock_section(story, pick, f):
    ccy = pick.get("currency") or "—"
    """Stock-specific content: fundamentals, dividends, cost."""
    story.append(_subsection("Fundamentals"))
    # Absolute-dollar figures are reported in the security's own listing currency,
    # which is not necessarily the CAD the portfolio is sized in.
    story.append(_kv_table([
        ("P/E (trailing)",    _num(f.get("pe"), ".1f")),
        ("Forward P/E",       _num(f.get("forward_pe"), ".1f")),
        ("EPS",               f"{f['eps']:.2f} {ccy}" if f.get("eps") is not None else "—"),
        ("Revenue",           f"${f['revenue']:,.0f} {ccy}" if f.get("revenue") else "—"),
        ("Revenue growth",    _pct(f.get("revenue_growth"))),
        ("Net income",        f"${f['net_income']:,.0f} {ccy}" if f.get("net_income") else "—"),
        ("Net margin",        _pct(f.get("net_margin"))),
        ("Total assets",      f"${f['total_assets']:,.0f} {ccy}" if f.get("total_assets") else "—"),
        ("Debt / Equity",     _num(f.get("debt_equity"), ".2f")),
        ("Current ratio",     _num(f.get("current_ratio"), ".2f")),
    ]))
    story.append(_sp(8))

    story.append(_subsection("Dividends"))
    div_hist = f.get("dividend_history", {}) or {}
    story.append(_kv_table([
        ("Dividend yield (trailing)", _pct(f.get("dividend_yield"))),
        ("Annual div / share (forward run-rate)",
         f"${f['annual_div_per_share']:.2f} {ccy}" if f.get("annual_div_per_share") else "—"),
        ("Ex-dividend date",     f.get("ex_dividend_date") or "—"),
    ]))
    if div_hist:
        try:
            dh_buf = dividend_history_chart(div_hist, pick["symbol"])
            story.append(_sp(4))
            story.append(_img(dh_buf, CONTENT_W, _WIDE_H * 0.8))
        except Exception as exc:
            logger.warning("dividend_history_chart(%s): %s", pick["symbol"], exc)
    story.append(_sp(8))

    _cost_box(story, pick, f)


def _etf_section(story, pick, f):
    ccy = pick.get("currency") or "—"
    """ETF-specific content: details, holdings, sector pie, cost."""
    story.append(_subsection("ETF Details"))
    aum = f.get("aum")
    story.append(_kv_table([
        ("Expense ratio (MER)", f"{f['expense_ratio_raw']*100:.2f}%"
                                if f.get("expense_ratio_raw") else "Not available"),
        ("AUM",                 (f"${aum/1e9:.1f}B {ccy}" if aum >= 1e9 else f"${aum/1e6:.0f}M {ccy}") if aum else "—"),
        ("Distribution yield",  _pct(f.get("distribution_yield"))),
        ("Beta",                _num(f.get("beta"), ".2f")),
    ]))
    story.append(_sp(8))

    # Sector weights pie
    sw = f.get("sector_weights") or {}
    if sw:
        story.append(_subsection("Sector Allocation"))
        try:
            sp_buf = sector_pie_chart(sw, pick["symbol"])
            story.append(_img(sp_buf, CONTENT_W, _WIDE_H))
        except Exception as exc:
            logger.warning("sector_pie_chart(%s): %s", pick["symbol"], exc)
        story.append(_sp(6))

    # Top holdings
    holdings = f.get("top_holdings") or []
    if holdings:
        story.append(_subsection("Top Holdings"))
        rows = [[h.get("symbol","?"), h.get("name","")[:28], f"{h.get('weight',0):.1%}"]
                for h in holdings[:10]]
        story.append(_data_table(["Symbol","Name","Weight"], rows,
            col_widths=[52, CONTENT_W-52-60, 60]))
        story.append(_sp(6))

    # Distribution history
    dist_hist = f.get("distribution_history", {}) or {}
    if dist_hist:
        try:
            dh_buf = dividend_history_chart(dist_hist, pick["symbol"])
            story.append(_subsection("Distribution History"))
            story.append(_img(dh_buf, CONTENT_W, _WIDE_H * 0.8))
        except Exception as exc:
            logger.warning("distribution_history(%s): %s", pick["symbol"], exc)
        story.append(_sp(6))

    _cost_box(story, pick, f)


def _cost_box(story, pick, f):
    """Render cost box for stock or ETF."""
    cost = f.get("cost", {}) or {}
    story.append(_subsection("Cost"))

    mer  = cost.get("expense_ratio")
    drag = cost.get("annual_drag_per_10k")
    eff  = cost.get("cost_efficiency")
    ccy  = cost.get("trading_cost_currency") or "CAD"
    trade = cost.get("trading_cost")

    pairs = []
    # MER rows are omitted entirely for stocks (no such thing) and shown as
    # unavailable — never 0.00% — for ETFs Yahoo has no expense ratio for.
    if pick.get("is_etf"):
        pairs.append(("Expense ratio (MER)",
                      cost.get("expense_ratio_pct") or "Not available"))
        pairs.append(("Annual drag / $10k",
                      f"${drag:.2f}" if drag is not None else "Not available"))
        if eff:
            pairs.append(("Cost efficiency", eff))
    pairs.append(("One-time trading",
                  f"${trade:.2f} {ccy} (approx.)" if trade is not None else "—"))
    pairs.append(("FX conversion",
                  "~1.50% on purchase (USD)" if cost.get("fx_conversion_applicable")
                  else "Not applicable (CAD-listed)"))

    story.append(_kv_table(pairs))
    if pick.get("is_etf") and mer is None:
        story.append(_small("MER unavailable from the data source for this fund — "
                            "check the provider's fund page before assuming cost."))
    story.append(_sp(8))

# ---------------------------------------------------------------------------
# Page 12 — Universe Overview
# ---------------------------------------------------------------------------

def _page_universe(story: list, all_scores: dict, risk_metrics: dict, user_risk: int,
                   risk_bar_png: bytes | None = None):
    story.append(_section("Universe Overview"))
    story.append(_sp(6))

    if risk_bar_png:
        # Clamp to the frame — len(all_scores) * 14 overflows the page past ~49 tickers.
        img_h = min(_MAX_IMG_H, max(_WIDE_H, len(all_scores) * 14))
        story.append(_img(io.BytesIO(risk_bar_png), CONTENT_W, img_h))
    story.append(_sp(10))

    story.append(_subsection("All Tickers — Ranked by Risk Score"))
    # all_scores is already normalised to {"risk_score": float} by generate_pdf
    sorted_tickers = sorted(
        ((sym, v["risk_score"]) for sym, v in all_scores.items()),
        key=lambda x: x[1],
    )

    rows = []
    for sym, rs in sorted_tickers:
        sharpe = (risk_metrics.get(sym) or {}).get("sharpe_ratio")
        rows.append([sym, _num(rs, ".1f"), _num(sharpe, ".2f")])

    story.append(_data_table(
        ["Symbol", "Risk Score (1–10)", "Sharpe Ratio"],
        rows,
        col_widths=[80, CONTENT_W*0.45, CONTENT_W*0.45 - 80],
    ))

# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def generate_pdf(
    output_path: str,
    picks: list,
    macro_context: dict,
    scenario_result: dict,
    ticker_data: dict,
    risk_scores: dict,
    risk_metrics: dict,
    rec_result: dict,
    user_risk: int,
    amount: int = 50_000,
    platform: str = "questrade",
) -> None:
    """Generate the 12-page PDF research report.

    Args:
        output_path:     Destination .pdf file path.
        picks:           Assembled picks list from recommend().
        macro_context:   Output of fetch_macro_context().
        scenario_result: Output of run_scenarios().
        ticker_data:     Raw data dict from DataFetcher.fetch_all() (for charts).
        risk_scores:     {symbol: {"risk_score": float, ...}} for all tickers.
        risk_metrics:    {symbol: {"sharpe_ratio": float, ...}} for all tickers.
        rec_result:      Full output dict from recommend().
        user_risk:       Integer 1–10.
        amount:          Portfolio size in CAD.
        platform:        "questrade" | "wealthsimple" | "td_direct".
    """
    doc = SimpleDocTemplate(
        output_path,
        pagesize=letter,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN,  bottomMargin=MARGIN,
    )

    story = []

    # All scores dict (normalised)
    all_scores = {sym: (v if isinstance(v, dict) else {"risk_score": float(v or 0)})
                  for sym, v in risk_scores.items()}

    # Rendered once — pages 3 and 12 embed the same chart.
    risk_bar_png = None
    if all_scores:
        try:
            risk_bar_png = universe_risk_bar(all_scores, user_risk).getvalue()
        except Exception as exc:
            logger.warning("universe_risk_bar failed: %s", exc)

    _page_cover(story, user_risk)
    _page_macro(story, macro_context, user_risk)
    _page_risk_profile(story, user_risk, all_scores, risk_bar_png)
    _page_recommendations(story, picks)
    _page_allocation(story, picks, rec_result, ticker_data)
    _page_scenarios(story, scenario_result)

    for pick in picks:
        _page_ticker(story, pick, ticker_data)

    _page_universe(story, all_scores, risk_metrics, user_risk, risk_bar_png)

    doc.build(story)
    logger.info("PDF written to %s", output_path)
    print(f"PDF saved → {output_path}")
