export interface MetricHelp {
  label: string
  description: string
}

export const METRIC_HELP: MetricHelp[] = [
  {
    label: 'P/E (Price-to-Earnings)',
    description: 'Share price divided by trailing 12-month earnings per share. Shows how many dollars investors pay for $1 of current earnings — higher usually means the market expects more growth (or the stock is expensive).',
  },
  {
    label: 'Fwd P/E (Forward P/E)',
    description: 'Same idea as P/E, but uses analysts’ estimated earnings for the next 12 months instead of trailing earnings. A forward P/E lower than the trailing P/E implies earnings are expected to grow.',
  },
  {
    label: 'PEG Ratio',
    description: 'P/E ratio divided by the expected earnings growth rate. A PEG near 1 suggests the valuation is in line with growth; well above 1 can mean the stock is expensive relative to its growth, below 1 can mean it’s cheap.',
  },
  {
    label: 'D/E (Debt-to-Equity)',
    description: 'Total debt divided by shareholder equity. Measures how much the company relies on borrowed money vs. its own capital — higher values mean more leverage and more financial risk.',
  },
  {
    label: 'Quick Ratio',
    description: 'Cash, short-term investments, and receivables divided by current liabilities. A stricter liquidity test than the current ratio since it excludes inventory. Above 1 generally means the company can cover short-term obligations without selling inventory.',
  },
  {
    label: 'Current Ratio',
    description: 'Current assets divided by current liabilities. Measures whether a company has enough short-term assets to cover its short-term debts — above 1 is generally considered healthy.',
  },
  {
    label: 'ROE (Return on Equity)',
    description: 'Net income divided by shareholder equity. Shows how efficiently a company turns shareholders’ capital into profit — higher is generally better, though a very high ROE can also signal heavy debt.',
  },
  {
    label: 'ROA (Return on Assets)',
    description: 'Net income divided by total assets. Shows how efficiently a company uses everything it owns to generate profit, independent of how it’s financed.',
  },
  {
    label: 'Interest Coverage',
    description: 'EBIT (operating earnings) divided by interest expense. Shows how many times over a company can pay the interest on its debt from operating earnings — lower values signal more risk of financial distress. Shown as – when a company reports no interest expense (e.g. most banks).',
  },
  {
    label: 'Rev CAGR (3y)',
    description: 'Compound annual growth rate of revenue over the last 3 fiscal years. Smooths out one-off spikes or dips to show the underlying growth trend rather than a single year’s change.',
  },
  {
    label: 'EPS CAGR (3y)',
    description: 'Compound annual growth rate of earnings per share over the last 3 fiscal years — the same idea as revenue CAGR, applied to profit per share.',
  },
]
