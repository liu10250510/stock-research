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
