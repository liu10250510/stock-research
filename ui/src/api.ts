import type { JobParams, CustomJobParams, PerformanceResponse } from './types'

const BASE = '/api'

export async function getSectors(): Promise<string[]> {
  const r = await fetch(`${BASE}/sectors`)
  if (!r.ok) throw new Error('Failed to load sectors')
  const data = await r.json()
  return data.sectors as string[]
}

export async function createJob(params: JobParams): Promise<string> {
  const r = await fetch(`${BASE}/jobs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Unknown error' }))
    const detail = Array.isArray(err.detail)
      ? err.detail.map((e: { msg: string }) => e.msg).join('; ')
      : String(err.detail ?? 'Unknown error')
    throw new Error(detail)
  }
  const data = await r.json()
  return data.job_id as string
}

export async function createCustomJob(params: CustomJobParams): Promise<string> {
  const r = await fetch(`${BASE}/custom-jobs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Unknown error' }))
    const detail = Array.isArray(err.detail)
      ? err.detail.map((e: { msg: string }) => e.msg).join('; ')
      : String(err.detail ?? 'Unknown error')
    throw new Error(detail)
  }
  return (await r.json()).job_id as string
}

export function getReportUrl(jobId: string): string {
  return `${BASE}/jobs/${jobId}/report`
}

export async function getPerformanceMetrics(symbols: string[]): Promise<PerformanceResponse> {
  const r = await fetch(`${BASE}/performance`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ symbols }),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Unknown error' }))
    const detail = Array.isArray(err.detail)
      ? err.detail.map((e: { msg: string }) => e.msg).join('; ')
      : String(err.detail ?? 'Unknown error')
    throw new Error(detail)
  }
  return r.json()
}
