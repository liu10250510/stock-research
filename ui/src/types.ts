export type Platform = 'questrade' | 'wealthsimple' | 'td_direct'

export interface CustomJobParams {
  symbols: string[]
  risk: number
  amount: number
  platform: Platform
  output: string
}
export type JobStatus = 'pending' | 'running' | 'done' | 'error'

export interface JobParams {
  risk: number
  amount: number
  platform: Platform
  universe_size: number
  sectors: string[] | null
  include: string[] | null
  exclude: string[] | null
  output: string
}

export interface Job {
  job_id: string
  status: JobStatus
  created_at: number
  params: JobParams
  log_lines: string[]
  error: string | null
  output_path: string | null
}

export interface PerformanceMetrics {
  symbol: string
  name: string | null
  sector: string | null
  is_etf: boolean
  pe: number | null
  forward_pe: number | null
  peg_ratio: number | null
  debt_equity: number | null
  quick_ratio: number | null
  current_ratio: number | null
  roe: number | null
  roa: number | null
  interest_coverage: number | null
  revenue_cagr_3y: number | null
  eps_cagr_3y: number | null
}

export interface PerformanceResponse {
  results: PerformanceMetrics[]
  skipped: string[]
}

export interface MarketMove {
  symbol: string
  name: string
  group: string
  kind: string
  /** "pct" → read `return`; "level" → read `change` (a yield/VIX move is not a return). */
  unit: 'pct' | 'level'
  level: number | null
  return: number | null
  change: number | null
}

export interface MarketHeadline {
  snippet: string
  url: string
  domain: string
  sentiment_label: string
}

export interface SectorRow {
  symbol: string
  sector: string
  market: 'US' | 'Canada'
  style: 'cyclical' | 'defensive' | 'sensitive'
  return: number
}

export interface SectorLeader {
  sector: string
  market: string
  return: number
}

export interface SectorDivergence {
  sector: string
  canada: number
  us: number
  gap: number
}

/** Every field here records what already happened — none of it is a forecast. */
export interface TrendSignals {
  leaders: SectorLeader[]
  laggards: SectorLeader[]
  breadth: number | null
  breadth_label: string | null
  risk_spread: number | null
  risk_label: string | null
  divergences: SectorDivergence[]
  meaningful_count: number
}

export interface CrossTrendItem {
  sector: string
  market: string
  returns?: Record<string, number>
  short?: number
  month?: number
}

export interface CrossWindowTrends {
  sustained_strength: CrossTrendItem[]
  sustained_weakness: CrossTrendItem[]
  rotating_in: CrossTrendItem[]
  rotating_out: CrossTrendItem[]
}

export interface MarketWindow {
  label: string
  sectors: SectorRow[]
  trends: TrendSignals
  moves: Record<string, MarketMove>
  narrative: string | null
  /** Which engine wrote `narrative` — surfaced in the UI so readers know. */
  summarizer: 'claude' | 'extractive' | null
  headlines: MarketHeadline[]
  sources: string[]
  sentiment_score: number
  sentiment_label: string
  /** True when no usable news came back; show the moves, not a fabricated story. */
  data_unavailable: boolean
}

export interface MarketSummary {
  as_of: string
  windows: Record<string, MarketWindow>
  cross_window_trends: CrossWindowTrends
  errors: string[]
}
