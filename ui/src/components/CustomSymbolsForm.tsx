import { useState } from 'react'
import { createCustomJob } from '../api'
import type { CustomJobParams } from '../types'

interface Props {
  onJobStarted: (jobId: string, output: string) => void
  disabled: boolean
}

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

export default function CustomSymbolsForm({ onJobStarted, disabled }: Props) {
  const [symbolsRaw, setSymbolsRaw] = useState('')
  const [output, setOutput]         = useState('custom_report.pdf')
  const [error, setError]           = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

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
    setSubmitting(true)
    const normalizedOutput = output.endsWith('.pdf') ? output : output + '.pdf'
    const params: CustomJobParams = {
      symbols: parsed,
      risk: 5,
      amount: 50_000,
      platform: 'questrade',
      output: normalizedOutput,
    }
    try {
      const jobId = await createCustomJob(params)
      onJobStarted(jobId, normalizedOutput)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start job')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-0">

      {/* ── Section 1 ─────────────────────────────── */}
      <SectionLabel>Symbols</SectionLabel>

      <div className="mb-8">
        <div className="flex items-center justify-between mb-1.5">
          <label className="text-[13px] font-semibold text-slate-600">
            Enter symbols to analyse
          </label>
          <span className="text-xs text-slate-400">one per line, or comma-separated · max 50</span>
        </div>
        <textarea
          rows={6}
          value={symbolsRaw}
          onChange={e => setSymbolsRaw(e.target.value)}
          disabled={disabled}
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

      {/* ── Section 2 ─────────────────────────────── */}
      <div className="pt-8">
        <SectionLabel>Output</SectionLabel>
      </div>

      <div className="mb-8">
        <label className="block text-[13px] font-semibold text-slate-600 mb-1.5">
          Filename
        </label>
        <input
          type="text" value={output}
          onChange={e => setOutput(e.target.value)}
          disabled={disabled}
          className={inputCls}
        />
      </div>

      {/* Error */}
      {error && (
        <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-xl px-4 py-2.5 mb-6">
          {error}
        </p>
      )}

      {/* Submit */}
      <button
        type="submit"
        disabled={disabled || submitting || parsed.length === 0}
        className="w-full py-3.5 rounded-xl text-white font-bold text-[15px] flex items-center justify-center gap-2 transition-opacity disabled:opacity-50"
        style={{ background: BRAND, boxShadow: '0 4px 16px rgba(14,164,114,0.35)' }}
      >
        {submitting ? 'Starting…' : <>Generate Report <span>→</span></>}
      </button>
    </form>
  )
}
