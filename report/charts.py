"""
Chart generation (Step 10, §15).

All public functions return io.BytesIO containing a PNG.
The caller is responsible for closing the BytesIO after embedding in the PDF.

Style: 8×3 in (wide panels), 10×4 for price charts. No titles.
Font: sans-serif. Color palette from §16.1.

Public API:
    price_chart(history, symbol)                -> BytesIO
    rsi_chart(history, symbol)                  -> BytesIO
    forecast_chart(current, forecast_data, sym) -> BytesIO
    dividend_history_chart(history, symbol)      -> BytesIO
    sector_pie_chart(sector_weights, symbol)     -> BytesIO
    portfolio_allocation_pie(picks)              -> BytesIO
    universe_risk_bar(all_scores, user_risk)     -> BytesIO
    correlation_heatmap(corr_matrix, symbols)  ★ -> BytesIO
    scenario_bar_chart(scenarios)              ★ -> BytesIO
    sentiment_gauge(sentiment_score, symbol)   ★ -> BytesIO
    risk_score_breakdown(components, symbol)   ★ -> BytesIO
"""

import io
import logging
import math
import warnings
warnings.filterwarnings("ignore", message=".*Tight layout.*")

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd

from recommendations.engine import _band_for_risk

logger = logging.getLogger(__name__)

# Max height in inches for universe_risk_bar — keeps the figure inside the PDF frame
# even for an 85-ticker universe.
_RISK_BAR_MAX_H = 8.0

# ---------------------------------------------------------------------------
# §16.1  Color palette
# ---------------------------------------------------------------------------

C_NAVY   = "#1B3A6B"
C_BLUE   = "#2E86AB"
C_ORANGE = "#F18F01"
C_GREEN  = "#28A745"
C_RED    = "#DC3545"
C_AMBER  = "#FFC107"
C_LGRAY  = "#F5F5F5"
C_DTEXT  = "#333333"

_FONT = "DejaVu Sans"
plt.rcParams.update({
    "font.family":       "sans-serif",
    "font.size":         9,
    "axes.spines.top":   False,
    "axes.spines.right": False,
    "axes.edgecolor":    "#CCCCCC",
    "axes.labelcolor":   C_DTEXT,
    "xtick.color":       C_DTEXT,
    "ytick.color":       C_DTEXT,
    "figure.facecolor":  "white",
    "axes.facecolor":    "white",
})

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fig_to_bytes(fig) -> io.BytesIO:
    # Every chart reserves its own space (tight_layout, or subplots_adjust for the
    # pies), so bbox_inches="tight" was a redundant extra layout pass. Omitting it
    # also bounds the raster to the figure size — a stray off-canvas artist can no
    # longer inflate the saved image. Charts with an outside-axes legend must keep
    # their labels short; see _trim_label.
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=80)
    plt.close(fig)
    buf.seek(0)
    return buf


def _new_fig(w=8, h=3):
    return plt.subplots(figsize=(w, h))


# Legends anchored outside the axes only have the width reserved by
# subplots_adjust(right=0.68); ~40 chars is what fits at fontsize 8.
_LABEL_MAX = 38


def _trim_label(text: str) -> str:
    text = str(text)
    return text if len(text) <= _LABEL_MAX else text[:_LABEL_MAX - 1] + "…"


def _pct_fmt(x, _):
    return f"{x:.0%}"


# ---------------------------------------------------------------------------
# price_chart  §15
# ---------------------------------------------------------------------------

def price_chart(history: pd.DataFrame, symbol: str) -> io.BytesIO:
    """12-month Close + MA50 (orange) + MA200 (red). Size 10×4."""
    fig, ax = _new_fig(10, 4)
    try:
        close = history["Close"].dropna()
        close_1y = close.iloc[-252:] if len(close) >= 252 else close

        ax.plot(close_1y.index, close_1y.values, color=C_NAVY, linewidth=1.2, label="Close")

        if len(close) >= 50:
            ma50 = close.rolling(50).mean().iloc[-252:]
            ax.plot(ma50.index, ma50.values, color=C_ORANGE, linewidth=1,
                    linestyle="--", label="MA50", alpha=0.9)

        if len(close) >= 200:
            ma200 = close.rolling(200).mean().iloc[-252:]
            ax.plot(ma200.index, ma200.values, color=C_RED, linewidth=1,
                    linestyle="--", label="MA200", alpha=0.9)

        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))
        ax.legend(fontsize=8, frameon=False)
        ax.tick_params(axis="x", rotation=20)
        ax.set_ylabel(f"{symbol} Price")
        fig.tight_layout()
    except Exception as exc:
        logger.warning("price_chart(%s): %s", symbol, exc)
        ax.text(0.5, 0.5, "Chart unavailable", ha="center", va="center",
                transform=ax.transAxes, color=C_DTEXT)
    return _fig_to_bytes(fig)


# ---------------------------------------------------------------------------
# rsi_chart  §15
# ---------------------------------------------------------------------------

def rsi_chart(history: pd.DataFrame, symbol: str) -> io.BytesIO:
    """14-day RSI line + overbought/oversold bands. Size 8×3."""
    fig, ax = _new_fig(8, 3)
    try:
        close = history["Close"].dropna()
        close_1y = close.iloc[-252:] if len(close) >= 252 else close

        delta = close_1y.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rs    = gain / loss.replace(0, np.nan)
        rsi   = (100 - (100 / (1 + rs))).dropna()

        ax.plot(rsi.index, rsi.values, color=C_BLUE, linewidth=1.2)
        ax.axhline(70, color=C_RED,   linestyle="--", linewidth=0.8, alpha=0.7)
        ax.axhline(30, color=C_GREEN, linestyle="--", linewidth=0.8, alpha=0.7)
        ax.fill_between(rsi.index, 70, 100, alpha=0.07, color=C_RED)
        ax.fill_between(rsi.index, 0,  30,  alpha=0.07, color=C_GREEN)

        ax.set_ylim(0, 100)
        ax.set_ylabel("RSI (14)")
        ax.text(rsi.index[-1], 72, "Overbought", fontsize=7, color=C_RED,
                ha="right", va="bottom")
        ax.text(rsi.index[-1], 28, "Oversold", fontsize=7, color=C_GREEN,
                ha="right", va="top")
        ax.tick_params(axis="x", rotation=20)
        fig.tight_layout()
    except Exception as exc:
        logger.warning("rsi_chart(%s): %s", symbol, exc)
        ax.text(0.5, 0.5, "Chart unavailable", ha="center", va="center",
                transform=ax.transAxes, color=C_DTEXT)
    return _fig_to_bytes(fig)


# ---------------------------------------------------------------------------
# forecast_chart  §15
# ---------------------------------------------------------------------------

def forecast_chart(current: float, forecast_data: dict, symbol: str) -> io.BytesIO:
    """Current price → 3m forecast dot with analyst high/low band. Size 8×3."""
    fig, ax = _new_fig(8, 3)
    try:
        forecast_3m  = forecast_data.get("forecast_3m")
        forecast_high = forecast_data.get("forecast_high")
        forecast_low  = forecast_data.get("forecast_low")

        x = [0, 1]
        labels = ["Now", "3 Months"]

        if current is not None and forecast_3m is not None:
            color = C_GREEN if forecast_3m >= current else C_RED
            ax.plot(x, [current, forecast_3m], color=color, linewidth=2,
                    marker="o", markersize=7, zorder=3)

            # 12-month analyst high/low, drawn at its true absolute levels and
            # offset from the 3-month point. Previously this was an error bar
            # centred on the 3-month forecast, so max(0.0, ...) silently collapsed
            # a whisker whenever the projection fell outside the analyst range —
            # hiding exactly the disagreement worth seeing.
            if forecast_high is not None and forecast_low is not None:
                ax.vlines(1.18, forecast_low, forecast_high,
                          color=C_BLUE, linewidth=1.4, alpha=0.75,
                          label="12m analyst range")
                for y in (forecast_low, forecast_high):
                    ax.hlines(y, 1.13, 1.23, color=C_BLUE, linewidth=1.4, alpha=0.75)

            upside = (forecast_3m / current - 1) * 100
            ax.annotate(f"${forecast_3m:,.2f}\n({upside:+.1f}%)",
                        xy=(1, forecast_3m), xytext=(10, 0),
                        textcoords="offset points", fontsize=8,
                        color=color, va="center")
            ax.annotate(f"${current:,.2f}",
                        xy=(0, current), xytext=(-10, 0),
                        textcoords="offset points", fontsize=8,
                        color=C_DTEXT, va="center", ha="right")

        ax.set_xticks(x)
        ax.set_xticklabels(labels)
        ax.set_xlim(-0.3, 1.5)
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
        ax.set_ylabel(f"{symbol} Price")
        # Upper-left: the analyst range bar occupies the right side.
        ax.legend(fontsize=8, frameon=False, loc="upper left")
        fig.tight_layout()
    except Exception as exc:
        logger.warning("forecast_chart(%s): %s", symbol, exc)
        ax.text(0.5, 0.5, "Chart unavailable", ha="center", va="center",
                transform=ax.transAxes, color=C_DTEXT)
    return _fig_to_bytes(fig)

# ---------------------------------------------------------------------------
# dividend_history_chart  §15
# ---------------------------------------------------------------------------

def dividend_history_chart(history: dict, symbol: str) -> io.BytesIO:
    """Annual/quarterly dividend bars. history = {period_label: amount}. Size 8×3."""
    fig, ax = _new_fig(8, 3)
    try:
        if not history:
            raise ValueError("No dividend history")

        # str() keeps the axis categorical. Annual history is keyed by int year
        # (fundamentals._dividend_history_annual), which would otherwise place the
        # bars at x≈2021–2025 while the labels below sat at x=0..4.
        labels = [str(k) for k in history.keys()]
        values = [float(v) if v is not None else 0.0 for v in history.values()]

        bars = ax.bar(labels, values, color=C_BLUE, width=0.5, zorder=2)
        offset = max(values) * 0.02
        for bar, val in zip(bars, values):
            if val > 0:
                ax.text(bar.get_x() + bar.get_width() / 2, val + offset, f"${val:.2f}",
                        ha="center", va="bottom", fontsize=7.5, color=C_DTEXT)

        ax.set_ylabel("Dividends / Distributions ($)")
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:.2f}"))
        ax.tick_params(axis="x", rotation=20)
        ax.grid(axis="y", linestyle=":", alpha=0.4)
        fig.tight_layout()
    except Exception as exc:
        logger.warning("dividend_history_chart(%s): %s", symbol, exc)
        ax.text(0.5, 0.5, "No dividend data", ha="center", va="center",
                transform=ax.transAxes, color=C_DTEXT)
    return _fig_to_bytes(fig)


# ---------------------------------------------------------------------------
# sector_pie_chart  §15
# ---------------------------------------------------------------------------

def sector_pie_chart(sector_weights: dict, symbol: str) -> io.BytesIO:
    """ETF sector allocation pie. sector_weights = {sector: float}. Size 8×3."""
    fig, ax = _new_fig(8, 3)
    try:
        if not sector_weights:
            raise ValueError("No sector weights")

        # Keep top 8, group the rest as "Other"
        sorted_items = sorted(sector_weights.items(), key=lambda x: x[1], reverse=True)
        if len(sorted_items) > 8:
            top    = sorted_items[:8]
            others = sum(v for _, v in sorted_items[8:])
            top.append(("Other", others))
        else:
            top = sorted_items

        labels = [k for k, _ in top]
        values = [v for _, v in top]

        palette = [C_NAVY, C_BLUE, C_ORANGE, C_GREEN, C_AMBER,
                   "#6C3483", "#1A5276", "#117A65", C_RED, "#555555"]
        colors  = palette[:len(values)]

        wedges, texts, autotexts = ax.pie(
            values, labels=None, colors=colors,
            autopct=lambda p: f"{p:.1f}%" if p >= 3 else "",
            startangle=90, pctdistance=0.75,
            wedgeprops={"linewidth": 0.5, "edgecolor": "white"},
        )
        for at in autotexts:
            at.set_fontsize(7.5)

        ax.legend(wedges, [_trim_label(l) for l in labels], loc="center left",
                  bbox_to_anchor=(1, 0.5), fontsize=8, frameon=False)
        ax.set_aspect("equal")
        fig.subplots_adjust(right=0.68)
    except Exception as exc:
        logger.warning("sector_pie_chart(%s): %s", symbol, exc)
        ax.text(0.5, 0.5, "No sector data", ha="center", va="center",
                transform=ax.transAxes, color=C_DTEXT)
    return _fig_to_bytes(fig)


# ---------------------------------------------------------------------------
# portfolio_allocation_pie  §15
# ---------------------------------------------------------------------------

def portfolio_allocation_pie(picks: list) -> io.BytesIO:
    """Portfolio allocation pie from list of picks. Size 8×3."""
    fig, ax = _new_fig(8, 3)
    try:
        if not picks:
            raise ValueError("No picks")

        labels = [p["symbol"] for p in picks]
        values = [p.get("weight", 0) for p in picks]

        palette = [C_NAVY, C_BLUE, C_ORANGE, C_GREEN, C_AMBER,
                   "#6C3483", "#1A5276", "#117A65", C_RED, "#555555",
                   "#D4AC0D", "#1ABC9C"]
        colors  = palette[:len(labels)]

        wedges, texts, autotexts = ax.pie(
            values, labels=None, colors=colors,
            autopct=lambda p: f"{p:.0f}%" if p >= 4 else "",
            startangle=90, pctdistance=0.75,
            wedgeprops={"linewidth": 0.5, "edgecolor": "white"},
        )
        for at in autotexts:
            at.set_fontsize(8)

        ax.legend(wedges, [f"{l} ({v}%)" for l, v in zip(labels, values)],
                  loc="center left", bbox_to_anchor=(1, 0.5),
                  fontsize=8, frameon=False)
        ax.set_aspect("equal")
        fig.subplots_adjust(right=0.68)
    except Exception as exc:
        logger.warning("portfolio_allocation_pie: %s", exc)
        ax.text(0.5, 0.5, "No portfolio data", ha="center", va="center",
                transform=ax.transAxes, color=C_DTEXT)
    return _fig_to_bytes(fig)


# ---------------------------------------------------------------------------
# universe_risk_bar  §15
# ---------------------------------------------------------------------------

def universe_risk_bar(all_scores: dict, user_risk: int) -> io.BytesIO:
    """Horizontal bars for all tickers color-coded vs user's risk band. Size 8×3."""
    # Capped so an 85-ticker universe doesn't produce a 24 in tall figure that
    # overflows the PDF frame.
    fig, ax = _new_fig(8, min(_RISK_BAR_MAX_H, max(3, len(all_scores) * 0.28)))
    try:
        if not all_scores:
            raise ValueError("No scores")

        # Normalise: accept either float or {"risk_score": float}
        scores = {
            sym: (v["risk_score"] if isinstance(v, dict) else float(v))
            for sym, v in all_scores.items()
            if v is not None
        }

        lo, hi = _band_for_risk(user_risk)
        sorted_items = sorted(scores.items(), key=lambda x: x[1])
        symbols = [s for s, _ in sorted_items]
        values  = [v for _, v in sorted_items]

        colors = []
        for v in values:
            if v < lo:
                colors.append("#AAAAAA")   # too conservative
            elif v > hi:
                colors.append(C_RED)        # too risky
            else:
                colors.append(C_GREEN)      # within band

        y_pos = range(len(symbols))
        ax.barh(list(y_pos), values, color=colors, height=0.6, zorder=2)
        ax.set_yticks(list(y_pos))
        ax.set_yticklabels(symbols, fontsize=7.5)
        ax.axvline(lo, color=C_GREEN, linestyle="--", linewidth=0.8, alpha=0.7)
        ax.axvline(hi, color=C_RED,   linestyle="--", linewidth=0.8, alpha=0.7)
        ax.set_xlabel("Risk Score (1–10)")
        ax.set_xlim(0, 10.5)
        ax.grid(axis="x", linestyle=":", alpha=0.4)

        legend_patches = [
            mpatches.Patch(color=C_GREEN, label="Within your risk band"),
            mpatches.Patch(color="#AAAAAA", label="Too conservative"),
            mpatches.Patch(color=C_RED,   label="Too risky"),
        ]
        ax.legend(handles=legend_patches, fontsize=7.5, frameon=False,
                  loc="lower right")
        fig.tight_layout()
    except Exception as exc:
        logger.warning("universe_risk_bar: %s", exc)
        ax.text(0.5, 0.5, "No score data", ha="center", va="center",
                transform=ax.transAxes, color=C_DTEXT)
    return _fig_to_bytes(fig)


# ---------------------------------------------------------------------------
# correlation_heatmap ★  §15
# ---------------------------------------------------------------------------

def correlation_heatmap(corr_matrix: pd.DataFrame, symbols: list) -> io.BytesIO:
    """Pairwise correlation heatmap; warm = high corr. Size 8×3 (square-ish)."""
    n   = max(len(symbols), 2)
    sz  = max(4, min(8, n * 0.8))
    fig, ax = _new_fig(sz, sz * 0.85)
    try:
        if corr_matrix is None or corr_matrix.empty:
            raise ValueError("Empty correlation matrix")

        mat = corr_matrix.loc[symbols, symbols].values if (
            all(s in corr_matrix.index for s in symbols)
        ) else corr_matrix.values

        im = ax.imshow(mat, cmap="RdYlGn_r", vmin=-1, vmax=1, aspect="auto")
        plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)

        ax.set_xticks(range(len(symbols)))
        ax.set_yticks(range(len(symbols)))
        ax.set_xticklabels(symbols, rotation=45, ha="right", fontsize=7.5)
        ax.set_yticklabels(symbols, fontsize=7.5)

        for i in range(len(symbols)):
            for j in range(len(symbols)):
                val = mat[i, j]
                if not np.isnan(val):
                    color = "white" if abs(val) > 0.6 else C_DTEXT
                    ax.text(j, i, f"{val:.2f}", ha="center", va="center",
                            fontsize=6.5, color=color)

        fig.tight_layout()
    except Exception as exc:
        logger.warning("correlation_heatmap: %s", exc)
        ax.text(0.5, 0.5, "Correlation data unavailable", ha="center", va="center",
                transform=ax.transAxes, color=C_DTEXT)
    return _fig_to_bytes(fig)


# ---------------------------------------------------------------------------
# scenario_bar_chart ★  §15
# ---------------------------------------------------------------------------

def scenario_bar_chart(scenarios: dict) -> io.BytesIO:
    """Grouped bars: portfolio return + best/worst pick for each scenario. Size 8×3."""
    fig, ax = _new_fig(8, 3)
    try:
        keys   = ["crash", "rate_hike", "bull_run", "recession"]
        labels = ["Market Crash", "Rate Hike", "Bull Run", "Recession"]

        port_returns = []
        best_returns = []
        worst_returns = []

        for key in keys:
            sc = scenarios.get(key, {})
            port_returns.append(sc.get("portfolio_return", 0.0))
            best_returns.append(sc.get("best_pick",  {}).get("return", 0.0) if sc.get("best_pick")  else 0.0)
            worst_returns.append(sc.get("worst_pick", {}).get("return", 0.0) if sc.get("worst_pick") else 0.0)

        x      = np.arange(len(labels))
        width  = 0.25

        def _bar_colors(vals):
            return [C_GREEN if v >= 0 else C_RED for v in vals]

        ax.bar(x - width, port_returns,  width, color=_bar_colors(port_returns),
               label="Portfolio", zorder=2, alpha=0.9)
        ax.bar(x,          best_returns,  width, color=C_BLUE,
               label="Best pick", zorder=2, alpha=0.75)
        ax.bar(x + width,  worst_returns, width, color=C_AMBER,
               label="Worst pick", zorder=2, alpha=0.75)

        ax.axhline(0, color="#AAAAAA", linewidth=0.8)
        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=8)
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(_pct_fmt))
        ax.set_ylabel("Estimated Return")
        ax.legend(fontsize=8, frameon=False)
        ax.grid(axis="y", linestyle=":", alpha=0.4)
        fig.tight_layout()
    except Exception as exc:
        logger.warning("scenario_bar_chart: %s", exc)
        ax.text(0.5, 0.5, "Scenario data unavailable", ha="center", va="center",
                transform=ax.transAxes, color=C_DTEXT)
    return _fig_to_bytes(fig)


# ---------------------------------------------------------------------------
# sentiment_gauge ★  §15
# ---------------------------------------------------------------------------

def sentiment_gauge(sentiment_score: float, symbol: str) -> io.BytesIO:
    """Semicircular dial from −1 (red) to +1 (green) with needle. Size 8×3."""
    fig, ax = plt.subplots(figsize=(5, 3), subplot_kw={"aspect": "equal"})
    ax.set_xlim(-1.3, 1.3)
    ax.set_ylim(-0.15, 1.2)
    ax.axis("off")

    try:
        score = float(sentiment_score) if sentiment_score is not None else 0.0
        score = max(-1.0, min(1.0, score))

        # Draw colored arc segments: red (-1→-0.33), amber (-0.33→0.33), green (0.33→1)
        _draw_gauge_arc(ax, -1.0,  -0.15, C_RED)
        _draw_gauge_arc(ax, -0.15,  0.15, C_AMBER)
        _draw_gauge_arc(ax,  0.15,  1.0,  C_GREEN)

        # Needle: angle = 180° (score=-1) → 0° (score=+1)
        angle_deg = 180 - (score + 1) / 2 * 180
        angle_rad = math.radians(angle_deg)
        nx = 0.75 * math.cos(angle_rad)
        ny = 0.75 * math.sin(angle_rad)
        ax.annotate("", xy=(nx, ny), xytext=(0, 0),
                    arrowprops={"arrowstyle": "-|>", "color": C_DTEXT,
                                "lw": 2, "mutation_scale": 15})
        ax.add_patch(plt.Circle((0, 0), 0.05, color=C_DTEXT, zorder=5))

        # Score label and sentiment label
        label = "Positive" if score > 0.15 else ("Negative" if score < -0.15 else "Neutral")
        color = C_GREEN if score > 0.15 else (C_RED if score < -0.15 else C_AMBER)
        ax.text(0, -0.12, f"{label}  ({score:+.2f})",
                ha="center", va="top", fontsize=10, color=color, fontweight="bold")

        # Scale labels
        ax.text(-1.1, 0, "−1", ha="center", va="center", fontsize=7.5, color=C_RED)
        ax.text( 1.1, 0, "+1", ha="center", va="center", fontsize=7.5, color=C_GREEN)
        ax.text(0, 0.95, "0",  ha="center", va="bottom", fontsize=7.5, color=C_DTEXT)

        fig.tight_layout()
    except Exception as exc:
        logger.warning("sentiment_gauge(%s): %s", symbol, exc)
        ax.text(0, 0.5, "Sentiment data unavailable",
                ha="center", va="center", fontsize=9, color=C_DTEXT)
    return _fig_to_bytes(fig)


def _draw_gauge_arc(ax, score_start: float, score_end: float, color: str,
                    r_inner=0.6, r_outer=0.95):
    """Draw a filled arc segment between two score values (−1 to +1)."""
    # Convert score to angle: score=-1 → 180°, score=+1 → 0°
    a_start = 180 - (score_end + 1) / 2 * 180
    a_end   = 180 - (score_start + 1) / 2 * 180

    theta = np.linspace(math.radians(a_start), math.radians(a_end), 40)
    xs = np.concatenate([r_outer * np.cos(theta),
                          r_inner * np.cos(theta[::-1])])
    ys = np.concatenate([r_outer * np.sin(theta),
                          r_inner * np.sin(theta[::-1])])
    ax.fill(xs, ys, color=color, alpha=0.85, zorder=2)


# ---------------------------------------------------------------------------
# risk_score_breakdown ★  §15
# ---------------------------------------------------------------------------

def risk_score_breakdown(components: dict, symbol: str) -> io.BytesIO:
    """Horizontal bar chart of risk score components. Size 8×3."""
    fig, ax = _new_fig(8, 3)
    try:
        if not components:
            raise ValueError("No component data")

        label_map = {
            "beta":          "Beta",
            "volatility":    "Volatility",
            "debt_equity":   "Debt / Equity",
            "pe_ratio":      "P/E Ratio",
            "market_cap":    "Market Cap",
            "profit_margin": "Profit Margin",
        }

        items = [
            (label_map.get(k, k), v)
            for k, v in components.items()
            if v is not None
        ]
        if not items:
            raise ValueError("All components None")

        labels = [i[0] for i in items]
        values = [i[1] for i in items]

        # Color: green (low risk ≤4), amber (4-6), red (high risk >6)
        colors = [
            C_GREEN if v <= 4 else (C_AMBER if v <= 6 else C_RED)
            for v in values
        ]

        y_pos = range(len(labels))
        bars  = ax.barh(list(y_pos), values, color=colors, height=0.55, zorder=2)
        ax.set_yticks(list(y_pos))
        ax.set_yticklabels(labels, fontsize=8.5)

        for bar, val in zip(bars, values):
            ax.text(val + 0.1, bar.get_y() + bar.get_height() / 2,
                    f"{val:.1f}", va="center", fontsize=8, color=C_DTEXT)

        ax.set_xlim(0, 11)
        ax.set_xlabel("Component Score (1–10, higher = riskier)")
        ax.axvline(5, color="#AAAAAA", linestyle=":", linewidth=0.8)
        ax.grid(axis="x", linestyle=":", alpha=0.3)
        fig.tight_layout()
    except Exception as exc:
        logger.warning("risk_score_breakdown(%s): %s", symbol, exc)
        ax.text(0.5, 0.5, "Component data unavailable", ha="center", va="center",
                transform=ax.transAxes, color=C_DTEXT)
    return _fig_to_bytes(fig)
