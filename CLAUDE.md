# Stock Market Research Tool

## What this project does
Python CLI that fetches live market data for 30 well-known Canadian and US stocks/ETFs, scores each on a 1–10 risk scale, runs web search for analyst sentiment, produces 3-month price forecasts, and generates a multi-page PDF report with charts, fundamentals, dividends, costs, and a diversified portfolio allocation — designed for Canadian retail investors (Questrade, Wealthsimple, TD Direct Investing).

See [SPEC.md](SPEC.md) for full technical details, scoring formulas, and report layout.

## Commands

### Run
```bash
python main.py --risk 5 --output report.pdf                        # basic
python main.py --risk 3                                            # defaults to stock_report.pdf
python main.py --risk 8 --output aggressive.pdf
python main.py --risk 5 --amount 100000 --platform wealthsimple   # $100k portfolio, Wealthsimple fees
python main.py --risk 3 --amount 25000 --platform td_direct       # $25k, TD Direct fees
python main.py --risk 6 --universe-size 40                         # analyse 40 tickers
python main.py --risk 7 --sectors Technology Energy                # focus on two sectors
python main.py --risk 5 --include NVDA --exclude TSLA              # custom overrides
```

**Flags:**
- `--risk` (required) — 1–10
- `--output` — PDF filename (default `stock_report.pdf`)
- `--amount` — portfolio size in CAD for cost calculations (default `50000`)
- `--platform` — `questrade` (default), `wealthsimple`, or `td_direct`
- `--universe-size` — tickers to analyse from the pool (default `30`, max `85`)
- `--sectors` — space-separated sector names to restrict the pool (default: all 14 sectors)
- `--include` — space-separated symbols to force-include in the universe
- `--exclude` — space-separated symbols to force-exclude from the universe

### Install
```bash
pip install -r requirements.txt
```

### Test individual modules
```bash
python -m data.ticker_selector                          # preview default 30-ticker selection
python -m data.ticker_selector --size 40 --sectors Technology Energy
python -m data.ticker_selector --list-all               # show full 85-ticker pool

python -c "from data.ticker_selector import select_tickers; print(len(select_tickers()))"
python -c "from data.fetcher import DataFetcher; d = DataFetcher(); print(d.fetch_ticker('AAPL').keys())"
python -c "from analysis.risk_scorer import score_all_demo; score_all_demo()"
python -c "from analysis.forecast import forecast_demo; forecast_demo()"
python -c "from research.web_searcher import search_ticker; print(search_ticker('AAPL', 'Apple'))"
python -c "from recommendations.engine import engine_demo; engine_demo(3); engine_demo(8)"
python -c "from analysis.macro import macro_demo; macro_demo()"
python -c "from analysis.scenarios import scenarios_demo; scenarios_demo(5)"
```

## Project layout

```
stock_research/
├── main.py                   CLI entry point
├── requirements.txt
├── data/
│   ├── ticker_selector.py    86-ticker pool across 14 sectors; select_tickers() with
│   │                         proportional sector allocation; SECTOR_MAP, DISPLAY_NAMES,
│   │                         RISK_TIER_MAP, EXCHANGE_MAP, ETF_TICKERS lookups; CLI demo
│   └── fetcher.py            yfinance: 5yr history, info, balance sheet, income stmt,
│                             dividends, analyst recommendations, ETF funds_data
├── analysis/
│   ├── fundamentals.py       P/E, EPS, revenue, assets, margins, div yield, MER (ETFs),
│                             top holdings, sector weights, geographic focus
│   ├── technical.py          MA50/200, RSI-14, returns (1m/3m/1y/3y/5y),
│                             30d trading volume vs 90d baseline
│   ├── risk_scorer.py        Weighted score 1–10; ETF vs stock weights
│   ├── risk_metrics.py    ★  Sharpe ratio, Sortino ratio, max drawdown
│   ├── forecast.py           3-month forecast: analyst target model + momentum model
│   ├── macro.py           ★  Current macro context (rates/inflation) + sector modifiers
│   └── scenarios.py       ★  Portfolio stress tests: crash / rate hike / bull / recession
├── research/
│   └── web_searcher.py       DuckDuckGo search + sentiment scoring ★ + risk factor
│                             extraction ★ — no API key needed
├── recommendations/
│   └── engine.py             Risk band filter → risk-adjusted ranking ★ →
│                             correlation-aware diversification ★ →
│                             explainable reasoning ★ → portfolio weights
└── report/
    ├── charts.py             All matplotlib figures → BytesIO (+ correlation heatmap ★,
    │                         scenario bar ★, sentiment gauge ★, risk breakdown ★)
    └── pdf_generator.py      ReportLab Platypus — 12-page PDF report
```

## Core features (★ = v2.0 additions)

| # | Feature | SPEC.md |
|---|---|---|
| 1 | **Risk-adjusted recommendations** — picks ranked by Sharpe/Sortino, not just risk score | §8.4, §13.2 |
| 2 | **Diversification logic** — sector caps, correlation filter (>0.80), currency balance | §13.3 |
| 3 | **Explainable reasoning** — data-backed "Why this pick" bullets + "Watch out for" warnings | §11 |
| 4 | **Macro-aware analysis** — rate/inflation context applied as sector score modifiers | §12 |
| 5 | **Analyst consensus** — buy/hold/sell counts, upgrades/downgrades, consensus label | §9.4 |
| 6 | **Sentiment & risk extraction** — keyword sentiment score + named risk categories | §10 |
| 7 | **Portfolio scenario analysis** — crash / rate hike / bull run / recession stress tests | §14 |

**Other capabilities:** 3-month price forecast (§9), dividends & ETF costs (§7.3), stock vs ETF page formats (§16), Canadian platform fee estimates (§7.3).

## Data sources

- **Primary**: yfinance (Yahoo Finance) — free, no API key
- **Web research**: DuckDuckGo HTML search via `requests` + `BeautifulSoup` — free, no API key
- Prices are delayed ~15 minutes; noted on the report cover page

## Build steps

- [x] Step 1 — Project scaffold & ticker universe
- [x] Step 2 — Data fetcher
- [x] Step 3 — Fundamentals & technical analysis
- [x] Step 4 — Risk scorer + risk-adjusted metrics ★
- [x] Step 5 — Forecast & analyst consensus aggregation ★
- [x] Step 6 — Web research + sentiment scoring + risk factor extraction ★
- [x] Step 7 — Macro-aware analysis ★
- [x] Step 8 — Recommendation engine (risk-adjusted rank, correlation filter, explainability) ★
- [x] Step 9 — Portfolio scenario analysis ★
- [x] Step 10 — Charts (all incl. heatmap, scenario bar, sentiment gauge, risk breakdown) ★
- [x] Step 11 — PDF generator (12 pages)
- [x] Step 12 — Main CLI + end-to-end test
