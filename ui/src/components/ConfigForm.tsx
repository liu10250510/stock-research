import { useEffect, useRef, useState } from 'react'
import { getSectors, createJob } from '../api'
import type { JobParams } from '../types'

interface Props {
  onJobStarted: (jobId: string, output: string) => void
  disabled: boolean
}

const RISK_LABELS = [
  '', 'Very Conservative', 'Conservative', 'Moderately Conservative',
  'Moderate', 'Balanced', 'Moderately Aggressive', 'Aggressive',
  'Very Aggressive', 'Speculative', 'Maximum Risk',
]

const BRAND = '#0EA472'
const BRAND_LIGHT = '#E6F9F1'

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

export default function ConfigForm({ onJobStarted, disabled }: Props) {
  const [risk, setRisk]               = useState(5)
  const [universeSize, setUniverseSize] = useState(30)
  const [sectors, setSectors]         = useState<string[]>([])
  const [allSectors, setAllSectors]   = useState<string[]>([])
  const [sectorOpen, setSectorOpen]   = useState(false)
  const [include, setInclude]         = useState('')
  const [exclude, setExclude]         = useState('')
  const [output, setOutput]           = useState('stock_report.pdf')
  const [error, setError]             = useState<string | null>(null)
  const [submitting, setSubmitting]   = useState(false)
  const sectorRef                     = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (sectorRef.current && !sectorRef.current.contains(e.target as Node)) {
        setSectorOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  useEffect(() => { getSectors().then(setAllSectors).catch(() => setAllSectors([])) }, [])

  function toggleSector(s: string) {
    setSectors(prev => prev.includes(s) ? prev.filter(x => x !== s) : [...prev, s])
  }

  function parseSymbols(raw: string): string[] | null {
    const syms = raw.split(/[\s,]+/).map(s => s.trim().toUpperCase()).filter(Boolean)
    return syms.length ? syms : null
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    const normalizedOutput = output.endsWith('.pdf') ? output : output + '.pdf'
    const params: JobParams = {
      risk,
      amount: 50000,
      platform: 'questrade',
      universe_size: universeSize,
      sectors: sectors.length ? sectors : null,
      include: parseSymbols(include),
      exclude: parseSymbols(exclude),
      output: normalizedOutput,
    }
    try {
      const jobId = await createJob(params)
      onJobStarted(jobId, normalizedOutput)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start job')
    } finally {
      setSubmitting(false)
    }
  }

  // Slider fill % for CSS gradient
  const riskPct   = ((risk - 1) / 9) * 100
  const univPct   = ((universeSize - 10) / 75) * 100

  const inputCls = `w-full bg-slate-50 border border-slate-200 rounded-xl px-3 py-2.5 text-sm
    text-slate-800 outline-none transition
    focus:border-[#0EA472] focus:ring-2 focus:ring-[#0EA472]/10 focus:bg-white`

  return (
    <form onSubmit={handleSubmit} className="space-y-0">

      {/* ── Section 1 ─────────────────────────────── */}
      <SectionLabel>Portfolio Configuration</SectionLabel>

      {/* Risk */}
      <div className="mb-8">
        <div className="flex items-start gap-5">
          {/* Big number */}
          <div
            className="text-[52px] font-extrabold leading-none tabular-nums"
            style={{ color: BRAND, minWidth: 52, textAlign: 'center' }}
          >
            {risk}
          </div>
          {/* Slider + pips */}
          <div className="flex-1 pt-1">
            <p className="text-sm font-medium text-slate-500 mb-3">{RISK_LABELS[risk]}</p>
            <input
              type="range" min="1" max="10" value={risk}
              onChange={e => setRisk(Number(e.target.value))}
              disabled={disabled}
              className="w-full"
              style={{
                background: `linear-gradient(to right, ${BRAND} ${riskPct}%, #E2E8F0 ${riskPct}%)`,
              }}
            />
            {/* Pip track */}
            <div className="flex gap-1 mt-2">
              {Array.from({ length: 10 }, (_, i) => (
                <div
                  key={i}
                  className="flex-1 h-1 rounded-full transition-colors"
                  style={{ background: i < risk ? BRAND : '#E2E8F0' }}
                />
              ))}
            </div>
            <div className="flex justify-between text-[11px] text-slate-300 mt-1.5">
              <span>Conservative</span><span>Aggressive</span>
            </div>
          </div>
        </div>
      </div>

      {/* Universe size */}
      <div className="mt-6 mb-10">
        <div className="flex items-center justify-between mb-2">
          <label className="text-[13px] font-semibold text-slate-600">Universe Size</label>
          <span className="text-sm font-bold" style={{ color: BRAND }}>{universeSize} tickers</span>
        </div>
        <input
          type="range" min="10" max="85" step="5" value={universeSize}
          onChange={e => setUniverseSize(Number(e.target.value))}
          disabled={disabled}
          className="w-full"
          style={{
            background: `linear-gradient(to right, ${BRAND} ${univPct}%, #E2E8F0 ${univPct}%)`,
          }}
        />
        <div className="flex justify-between text-[11px] text-slate-300 mt-1.5">
          <span>10</span><span>85</span>
        </div>
      </div>

      {/* ── Section 2 ─────────────────────────────── */}
      <div className="pt-8">
        <SectionLabel>Filters &amp; Overrides</SectionLabel>
      </div>

      {/* Sectors dropdown */}
      {allSectors.length > 0 && (
        <div className="mt-5 mb-7" ref={sectorRef}>
          <div className="flex items-center justify-between mb-2.5">
            <label className="text-[13px] font-semibold text-slate-600">Sectors</label>
            <span className="text-xs text-slate-400">leave empty for all {allSectors.length}</span>
          </div>
          <div className="relative">
            <button
              type="button"
              onClick={() => !disabled && setSectorOpen(o => !o)}
              disabled={disabled}
              className={inputCls + ' flex items-center justify-between cursor-pointer'}
            >
              <span className={sectors.length ? 'text-slate-800' : 'text-slate-400'}>
                {sectors.length === 0 ? `All ${allSectors.length} sectors` : `${sectors.length} sector${sectors.length > 1 ? 's' : ''} selected`}
              </span>
              <svg className={`w-4 h-4 text-slate-400 transition-transform ${sectorOpen ? 'rotate-180' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
              </svg>
            </button>
            {sectorOpen && (
              <div className="absolute z-20 w-full mt-1 bg-white border border-slate-200 rounded-xl shadow-lg py-1 max-h-52 overflow-y-auto">
                {allSectors.map(s => (
                  <label key={s} className="flex items-center gap-2.5 px-3 py-2 hover:bg-slate-50 cursor-pointer select-none">
                    <input
                      type="checkbox"
                      checked={sectors.includes(s)}
                      onChange={() => toggleSector(s)}
                      className="accent-[#0EA472] w-3.5 h-3.5"
                    />
                    <span className="text-sm text-slate-700">{s}</span>
                  </label>
                ))}
              </div>
            )}
          </div>
          {sectors.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-2">
              {sectors.map(s => (
                <span key={s} className="flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium" style={{ background: BRAND_LIGHT, color: BRAND }}>
                  {s}
                  <button type="button" onClick={() => toggleSector(s)} className="hover:opacity-60 leading-none">×</button>
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Include / Exclude */}
      <div className="pt-8 mb-7">
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-[13px] font-semibold text-slate-600 mb-1.5">
              Force Include <span className="text-slate-400 font-normal">(symbols)</span>
            </label>
            <input
              type="text" value={include} placeholder="e.g. NVDA, AAPL"
              onChange={e => setInclude(e.target.value)}
              disabled={disabled}
              className={inputCls}
            />
          </div>
          <div>
            <label className="block text-[13px] font-semibold text-slate-600 mb-1.5">
              Force Exclude <span className="text-slate-400 font-normal">(symbols)</span>
            </label>
            <input
              type="text" value={exclude} placeholder="e.g. TSLA"
              onChange={e => setExclude(e.target.value)}
              disabled={disabled}
              className={inputCls}
            />
          </div>
        </div>
      </div>

      {/* ── Section 3 ─────────────────────────────── */}
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
        disabled={disabled || submitting}
        className="w-full py-3.5 rounded-xl text-white font-bold text-[15px] flex items-center justify-center gap-2 transition-opacity disabled:opacity-50"
        style={{ background: BRAND, boxShadow: '0 4px 16px rgba(14,164,114,0.35)' }}
      >
        {submitting ? 'Starting…' : <>Generate Report <span>→</span></>}
      </button>
    </form>
  )
}
