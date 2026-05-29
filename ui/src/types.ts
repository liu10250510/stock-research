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
