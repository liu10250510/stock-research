import { useMemo, useState } from 'react'
import type { PerformanceMetrics } from '../types'

interface Props {
  results: PerformanceMetrics[]
}

const BRAND = '#0EA472'

type Format = 'text' | 'ratio' | 'percent' | 'multiple'
type Key = keyof PerformanceMetrics

interface Column {
  key: Key
  label: string
  format: Format
}

const COLUMNS: Column[] = [
  { key: 'symbol',            label: 'Symbol',          format: 'text' },
  { key: 'name',              label: 'Name',            format: 'text' },
  { key: 'pe',                label: 'P/E',             format: 'ratio' },
  { key: 'forward_pe',        label: 'Fwd P/E',         format: 'ratio' },
  { key: 'peg_ratio',         label: 'PEG',             format: 'ratio' },
  { key: 'debt_equity',       label: 'D/E',             format: 'ratio' },
  { key: 'quick_ratio',       label: 'Quick Ratio',     format: 'ratio' },
  { key: 'current_ratio',     label: 'Current Ratio',   format: 'ratio' },
  { key: 'roe',               label: 'ROE',             format: 'percent' },
  { key: 'roa',               label: 'ROA',             format: 'percent' },
  { key: 'interest_coverage', label: 'Interest Cov.',   format: 'multiple' },
  { key: 'revenue_cagr_3y',   label: 'Rev CAGR (3y)',   format: 'percent' },
  { key: 'eps_cagr_3y',       label: 'EPS CAGR (3y)',   format: 'percent' },
]

function formatValue(value: PerformanceMetrics[Key], format: Format): string {
  if (value === null || value === undefined) return '–'
  if (format === 'text') return String(value)
  const n = Number(value)
  if (format === 'percent') return `${(n * 100).toFixed(1)}%`
  if (format === 'multiple') return `${n.toFixed(2)}x`
  return n.toFixed(2)
}

function sortValue(row: PerformanceMetrics, key: Key): [number, string | number] {
  const v = row[key]
  // Null/undefined sorts last regardless of direction (bucket 1 vs 0).
  if (v === null || v === undefined) return [1, '']
  return [0, typeof v === 'boolean' ? String(v) : v]
}

function csvField(value: string): string {
  return /[",\n]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value
}

function downloadCsv(rows: PerformanceMetrics[]) {
  const header = COLUMNS.map(col => col.label)
  const lines = rows.map(row => COLUMNS.map(col => formatValue(row[col.key], col.format)))
  const csv = [header, ...lines].map(line => line.map(csvField).join(',')).join('\n')

  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = 'performance_analysis.csv'
  link.click()
  URL.revokeObjectURL(url)
}

export default function PerformanceTable({ results }: Props) {
  const [sortKey, setSortKey] = useState<Key>('symbol')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc')

  function handleSort(key: Key) {
    if (key === sortKey) {
      setSortDir(d => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortKey(key)
      setSortDir('asc')
    }
  }

  const sorted = useMemo(() => {
    const copy = [...results]
    copy.sort((a, b) => {
      const [aBucket, aVal] = sortValue(a, sortKey)
      const [bBucket, bVal] = sortValue(b, sortKey)
      if (aBucket !== bBucket) return aBucket - bBucket
      if (aVal === bVal) return 0
      const cmp = aVal < bVal ? -1 : 1
      return sortDir === 'asc' ? cmp : -cmp
    })
    return copy
  }, [results, sortKey, sortDir])

  if (results.length === 0) return null

  return (
    <div className="mt-8">
      <div className="flex justify-end mb-3">
        <button
          type="button"
          onClick={() => downloadCsv(sorted)}
          className="flex items-center gap-2 text-sm font-bold px-4 py-2 rounded-xl transition-opacity hover:opacity-90"
          style={{ background: '#E6F9F1', color: '#0EA472' }}
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M7 10l5 5 5-5M12 15V3" />
          </svg>
          Download CSV
        </button>
      </div>

      <div className="overflow-x-auto rounded-xl border border-slate-200">
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="bg-slate-50">
              {COLUMNS.map(col => (
                <th
                  key={col.key}
                  onClick={() => handleSort(col.key)}
                  className="text-left font-semibold text-slate-500 text-xs uppercase tracking-wide px-3 py-2.5 whitespace-nowrap cursor-pointer select-none hover:text-slate-700"
                >
                  {col.label}
                  {sortKey === col.key && (
                    <span className="ml-1" style={{ color: BRAND }}>
                      {sortDir === 'asc' ? '↑' : '↓'}
                    </span>
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map(row => (
              <tr key={row.symbol} className="border-t border-slate-100 hover:bg-slate-50">
                {COLUMNS.map(col => (
                  <td
                    key={col.key}
                    className={
                      col.key === 'symbol'
                        ? 'px-3 py-2.5 font-bold text-slate-800 whitespace-nowrap'
                        : 'px-3 py-2.5 text-slate-600 whitespace-nowrap'
                    }
                  >
                    {formatValue(row[col.key], col.format)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
