# Stock Market Research Tool

A Python tool that fetches live market data, scores stocks on a 1–10 risk scale, runs web sentiment research, and generates a multi-page PDF report with charts, fundamentals, and a diversified portfolio allocation — designed for Canadian retail investors (Questrade, Wealthsimple, Major banks (TD Direct Investing)).

## Features

- **Risk-adjusted recommendations** — picks ranked by Sharpe/Sortino ratio
- **Diversification logic** — sector caps, correlation filter (>0.80), currency balance
- **Explainable reasoning** — data-backed "Why this pick" bullets and "Watch out for" warnings
- **Macro-aware analysis** — rate/inflation regime applied as sector score modifiers
- **Analyst consensus** — buy/hold/sell counts, upgrades/downgrades
- **Sentiment & risk extraction** — keyword sentiment score and named risk categories from web search
- **Portfolio scenario analysis** — crash / rate hike / bull run / recession stress tests
- **3-month price forecasts** — analyst target model + momentum model
- **Canadian platform fee estimates** — Questrade, Wealthsimple, Major banks (TD Direct Investing)

## Project layout

```
stock_research/
├── main.py                   CLI entry point + FastAPI job runner
├── api.py                    FastAPI backend (REST + SSE streaming)
├── requirements.txt
├── data/
│   ├── ticker_selector.py    85-ticker pool across 14 sectors
│   └── fetcher.py            yfinance data fetching
├── analysis/
│   ├── fundamentals.py       P/E, EPS, revenue, margins, dividends, ETF metrics
│   ├── technical.py          MA50/200, RSI-14, returns, volume
│   ├── risk_scorer.py        Weighted risk score 1–10
│   ├── risk_metrics.py       Sharpe ratio, Sortino ratio, max drawdown
│   ├── forecast.py           3-month price forecast
│   ├── macro.py              Macro regime context + sector modifiers
│   └── scenarios.py          Portfolio stress tests
├── research/
│   └── web_searcher.py       DuckDuckGo search + sentiment scoring
├── recommendations/
│   └── engine.py             Risk-adjusted ranking + diversification
├── report/
│   ├── charts.py             Matplotlib figures → BytesIO
│   └── pdf_generator.py      ReportLab PDF generation
└── ui/                       React + TypeScript frontend (Vite)
    ├── src/
    │   ├── App.tsx
    │   ├── api.ts
    │   ├── types.ts
    │   └── components/
    │       ├── ConfigForm.tsx
    │       ├── CustomSymbolsForm.tsx
    │       ├── DownloadButton.tsx
    │       └── ProgressPanel.tsx
    └── package.json
```

## Install

```bash
pip install -r requirements.txt
```

## CLI usage

```bash
python main.py --risk 5                                             # basic
python main.py --risk 3 --output report.pdf
python main.py --risk 5 --amount 100000 --platform wealthsimple
python main.py --risk 3 --amount 25000 --platform td_direct
python main.py --risk 6 --universe-size 40
python main.py --risk 7 --sectors Technology Energy
python main.py --risk 5 --include NVDA --exclude TSLA
```

**Flags:**

| Flag | Default | Description |
|------|---------|-------------|
| `--risk` | required | Risk tolerance 1–10 (1 = conservative, 10 = aggressive) |
| `--output` | `stock_report.pdf` | Output PDF filename |
| `--amount` | `50000` | Portfolio size in CAD |
| `--platform` | `questrade` | `questrade`, `wealthsimple`, or `td_direct` |
| `--universe-size` | `30` | Tickers to analyse (10–85) |
| `--sectors` | all | Space-separated sector names to restrict the pool |
| `--include` | — | Force-include these symbols |
| `--exclude` | — | Force-exclude these symbols |
| `--no-cache` | — | Bypass the 15-minute market-data cache and always fetch fresh |
| `--verbose` | — | Show INFO-level log messages |

## Web UI + API

Start the backend:

```bash
uvicorn api:app --reload --port 8000
```

Start the frontend (in a separate terminal):

```bash
cd ui && npm install && npm run dev
```

Open [http://localhost:5173](http://localhost:5173).

API endpoints:

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/sectors` | Available sector names |
| `POST` | `/api/jobs` | Start a universe-selection report job |
| `POST` | `/api/custom-jobs` | Start a custom-symbol report job |
| `GET` | `/api/jobs/{id}` | Poll job status and log |
| `GET` | `/api/jobs/{id}/stream` | SSE live log stream |
| `GET` | `/api/jobs/{id}/report` | Download generated PDF |

## Data sources

- **Market data**: [yfinance](https://github.com/ranaroussi/yfinance) (Yahoo Finance) — free, no API key
- **Web research**: DuckDuckGo HTML search via `requests` + `BeautifulSoup` — free, no API key
- Prices are delayed ~15 minutes; noted on the report cover page
