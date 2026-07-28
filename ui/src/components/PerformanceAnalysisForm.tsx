import { useState } from 'react'
import { getPerformanceMetrics } from '../api'
import type { PerformanceMetrics } from '../types'
import PerformanceTable from './PerformanceTable'
import MetricsHelpModal from './MetricsHelpModal'

const BRAND = '#0EA472'

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-3 mb-5">
      <div className="w-1 h-4 rounded-full shrink-0" style={{ background: BRAND }} />
      <span className="text-[11px] font-extrabold tracking-widest uppercase whitespace-nowrap" style={{ color: BRAND }}>
        {children}
      </span>
      <div className="flex-1 h-px" style={{ background: 'rgba(14,164,114,0.18)' }} />
    </div>
  )
}

function parseSymbols(raw: string): string[] {
  return [...new Set(
    raw.split(/[\s,\n]+/).map(s => s.trim().toUpperCase()).filter(Boolean)
  )]
}

export default function PerformanceAnalysisForm() {
  const [symbolsRaw, setSymbolsRaw] = useState('')
  const [error, setError]           = useState<string | null>(null)
  const [loading, setLoading]       = useState(false)
  const [results, setResults]       = useState<PerformanceMetrics[]>([])
  const [skipped, setSkipped]       = useState<string[]>([])
  const [showHelp, setShowHelp]     = useState(false)

  const parsed = parseSymbols(symbolsRaw)

  const inputCls = `w-full bg-slate-50 border border-slate-200 rounded-xl px-3 py-2.5 text-sm
    text-slate-800 outline-none transition
    focus:border-[#0EA472] focus:ring-2 focus:ring-[#0EA472]/10 focus:bg-white`

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    if (parsed.length === 0) {
      setError('Enter at least one stock symbol.')
      return
    }
    setLoading(true)
    setResults([])
    setSkipped([])
    try {
      const data = await getPerformanceMetrics(parsed)
      setResults(data.results)
      setSkipped(data.skipped)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch performance metrics')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div>
      <div className="flex justify-end mb-2">
        <button
          type="button"
          onClick={() => setShowHelp(true)}
          className="text-xs font-semibold flex items-center gap-1 hover:underline"
          style={{ color: BRAND }}
        >
          <span className="w-4 h-4 rounded-full flex items-center justify-center text-[10px] font-bold" style={{ background: '#E6F9F1' }}>?</span>
          What do these metrics mean?
        </button>
      </div>

      <form onSubmit={handleSubmit} className="space-y-0">
        <SectionLabel>Symbols</SectionLabel>

        <div className="mb-8">
          <div className="flex items-center justify-between mb-1.5">
            <label className="text-[13px] font-semibold text-slate-600">
              Enter symbols to compare
            </label>
            <span className="text-xs text-slate-400">one per line, or comma-separated · max 50</span>
          </div>
          <textarea
            rows={6}
            value={symbolsRaw}
            onChange={e => setSymbolsRaw(e.target.value)}
            disabled={loading}
            placeholder={"AAPL\nMSFT\nRY.TO\nTD.TO\nENB.TO"}
            className={inputCls + ' resize-none font-mono'}
          />
          {parsed.length > 0 && (
            <p className="mt-1.5 text-xs" style={{ color: BRAND }}>
              <span className="font-bold">{parsed.length} symbol{parsed.length !== 1 ? 's' : ''}:</span>{' '}
              {parsed.slice(0, 12).join(', ')}{parsed.length > 12 ? ` +${parsed.length - 12} more` : ''}
            </p>
          )}
        </div>

        {error && (
          <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-xl px-4 py-2.5 mb-6">
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={loading || parsed.length === 0}
          className="w-full py-3.5 rounded-xl text-white font-bold text-[15px] flex items-center justify-center gap-2 transition-opacity disabled:opacity-50"
          style={{ background: BRAND, boxShadow: '0 4px 16px rgba(14,164,114,0.35)' }}
        >
          {loading ? 'Analyzing…' : <>Analyze <span>→</span></>}
        </button>
      </form>

      {skipped.length > 0 && (
        <p className="mt-4 text-xs text-amber-600 bg-amber-50 border border-amber-200 rounded-xl px-4 py-2.5">
          Could not fetch: {skipped.join(', ')}
        </p>
      )}

      <PerformanceTable results={results} />

      <MetricsHelpModal open={showHelp} onClose={() => setShowHelp(false)} />
    </div>
  )
}
