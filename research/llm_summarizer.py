"""Turn market moves plus scraped headlines into a short narrative.

Two engines, and the caller always learns which one ran:

  * "extractive" — the default. Pure Python, no network, no key. Stitches the
    largest moves and the strongest-signal headlines into a couple of sentences.
  * "claude"     — used only when the `anthropic` package is installed AND
    credentials resolve. Produces real prose.

Every failure path in the Claude branch (missing package, missing credentials,
API error, refusal, empty response) falls back to extractive rather than raising.
Callers should still show a summary when there is no key and no network.

Public API:
    summarize(window_label, moves, headlines) -> (text, engine)
"""

import logging

logger = logging.getLogger(__name__)

# ALWAYS Claude Opus 5 unless the caller explicitly asks for something else.
MODEL = "claude-opus-5"

# Thinking is on by default on Claude Opus 5 and shares this budget with the
# response text — sized tightly around the expected prose, the answer truncates
# mid-sentence.
MAX_TOKENS = 4000

# Summarisation, not deep reasoning. Worth an effort sweep once there is real
# output to judge; see the plan's follow-ups.
EFFORT = "medium"

_SYSTEM_PROMPT = (
    "You write short sector-focused market recaps for Canadian retail investors.\n"
    "\n"
    "Rules, in priority order:\n"
    "1. Every figure given to you is exact. Never invent a number, never round "
    "one into a different number, and never state a figure that was not "
    "supplied.\n"
    "2. **The reader is already looking at a chart** of every sector return, "
    "plus labels for breadth, cyclical-vs-defensive positioning, and any "
    "Canada/US divergence. Do not list the leaders and laggards back to them, "
    "do not restate those labels, and do not recite percentages that are on the "
    "chart. Your job is what the chart cannot show: what the news says was "
    "behind the moves, and how the separate signals connect. Name a sector only "
    "when you are saying something about it that isn't already plotted.\n"
    "3. **Describe, do not predict.** The trend signals record what has already "
    "happened. You may explain what a signal means (for example, that "
    "defensives outpacing cyclicals is what risk-off positioning looks like). "
    "You may not forecast, project, or say what is 'likely' or 'poised' to "
    "happen next, and you may not tell the reader what to do about it.\n"
    "4. Attribute any explanation of *why* something moved to the supplied "
    "headlines only. If the headlines do not explain a move, say the move "
    "happened without asserting a cause.\n"
    "5. Be concise: at most 4 sentences. No preamble, no bullet points, no "
    "headings, no sign-off. If the headlines explain nothing about these "
    "sectors, say so in one sentence rather than padding with chart data.\n"
    "6. Plain prose for a non-professional reader. No jargon, no ticker symbols "
    "in place of names. Say 'Canadian energy' rather than 'XEG.TO'.\n"
    "7. This is a factual recap, not advice. Never recommend buying or selling."
)


def summarize(
    window_label: str,
    moves: dict,
    headlines: list,
    sectors: list | None = None,
    trends: dict | None = None,
    cross_trends: dict | None = None,
) -> tuple[str | None, str | None]:
    """Return (narrative, engine). engine is "claude" | "extractive" | None."""
    sectors      = sectors or []
    trends       = trends or {}
    cross_trends = cross_trends or {}

    if not headlines and not moves and not sectors:
        return None, None

    text = _summarize_with_claude(window_label, moves, headlines, sectors, trends, cross_trends)
    if text:
        return text, "claude"

    return _summarize_extractive(window_label, moves, headlines, sectors, trends), "extractive"


# ---------------------------------------------------------------------------
# Claude
# ---------------------------------------------------------------------------

def _summarize_with_claude(window_label: str, moves: dict, headlines: list,
                           sectors: list, trends: dict, cross_trends: dict) -> str | None:
    """Return prose, or None if Claude is unavailable for any reason."""
    try:
        import anthropic
    except ImportError:
        logger.debug("anthropic package not installed; using extractive summary.")
        return None

    try:
        # Resolves ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, or an `ant auth login`
        # profile. Raises if none of them are present.
        client = anthropic.Anthropic()
    except Exception as exc:
        logger.debug("No Anthropic credentials (%s); using extractive summary.", exc)
        return None

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            output_config={"effort": EFFORT},
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _build_prompt(
                window_label, moves, headlines, sectors, trends, cross_trends)}],
        )

        # A refusal is HTTP 200 with an empty or partial content list — check the
        # stop reason before indexing into content.
        if response.stop_reason == "refusal":
            logger.warning("Claude declined the market summary request; falling back.")
            return None

        text = next((b.text for b in response.content if b.type == "text"), None)
        return text.strip() if text and text.strip() else None

    except anthropic.APIStatusError as exc:
        logger.warning("Claude API error (%s); using extractive summary.", exc.status_code)
        return None
    except anthropic.APIConnectionError:
        logger.warning("Could not reach the Claude API; using extractive summary.")
        return None
    except Exception as exc:
        logger.warning("Claude summary failed (%s); using extractive summary.", exc)
        return None


def _build_prompt(window_label: str, moves: dict, headlines: list,
                  sectors: list, trends: dict, cross_trends: dict) -> str:
    lines = [f"Window: {window_label}"]

    if sectors:
        lines += ["", "SECTOR PERFORMANCE (exact, best to worst):"]
        for s in sectors:
            lines.append(f"- {s['sector']} ({s['market']}): {s['return']:+.2%}")

    if trends:
        lines += ["", "TREND SIGNALS (already-observed facts, not forecasts):"]
        if trends.get("breadth_label"):
            lines.append(f"- Breadth: {trends['breadth_label']} "
                         f"({trends.get('breadth', 0):.0%} of sectors positive)")
        if trends.get("risk_label"):
            lines.append(f"- Positioning: {trends['risk_label']}")
        for d in trends.get("divergences", []):
            lines.append(f"- Canada/US split in {d['sector']}: "
                         f"Canada {d['canada']:+.2%} vs US {d['us']:+.2%}")

    if cross_trends:
        for key, title in (("sustained_strength", "Positive in every window so far"),
                           ("sustained_weakness", "Negative in every window so far"),
                           ("rotating_in",  "Recently turned up after a weak month"),
                           ("rotating_out", "Recently turned down after a strong month")):
            items = cross_trends.get(key) or []
            if items:
                names = ", ".join(f"{i['sector']} ({i['market']})" for i in items)
                lines.append(f"- {title}: {names}")

    if moves:
        lines += ["", "MARKET BACKDROP (context only — exact, do not alter):"]
        for m in moves.values():
            if m.get("unit") == "level":
                chg = f"{m['change']:+.2f}" if m.get("change") is not None else "unchanged"
                lines.append(f"- {m['name']}: {m['level']:,.2f} ({chg})")
            elif m.get("return") is not None:
                lines.append(f"- {m['name']}: {m['return']:+.2%} (level {m['level']:,.2f})")

    if headlines:
        lines += ["", "HEADLINES (the only permitted basis for explaining any move):"]
        for h in headlines:
            lines.append(f"- [{h.get('domain','?')}] {h.get('snippet','')}")

    lines += ["", f"Write the commentary for: {window_label}. "
                  "The sector figures and signals above are already charted for the "
                  "reader — add what the chart cannot show, don't narrate it back. "
                  "Do not forecast."]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Extractive fallback
# ---------------------------------------------------------------------------

def _summarize_extractive(window_label: str, moves: dict, headlines: list,
                          sectors: list, trends: dict) -> str | None:
    """Deterministic, key-free digest of the parts the charts don't already show.

    Deliberately says nothing about sector leadership, breadth, risk tilt or
    Canada/US divergence: all four are rendered directly as bars, chips and
    callouts, so repeating them in prose is noise. What the charts cannot show is
    the news — that is all this returns.

    Returns None when there is nothing non-redundant to say, so the caller can
    omit the block entirely rather than print a stub.
    """
    if not headlines:
        return None

    domains = ", ".join(dict.fromkeys(h["domain"] for h in headlines[:3] if h.get("domain")))
    snippet = (headlines[0].get("snippet") or "").strip()
    if not snippet:
        return None

    return f"Coverage from {domains}: “{snippet}”" if domains else f"“{snippet}”"
