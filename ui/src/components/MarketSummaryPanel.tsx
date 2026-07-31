import { useEffect, useState } from 'react'
import { getMarketSummary } from '../api'
import type {
  MarketSummary, MarketWindow, MarketMove, SectorRow, CrossWindowTrends,
} from '../types'

const WINDOW_ORDER = ['1d', '1w', '1m']

const GREEN = '#0EA472'
const RED   = '#DC3545'
const GREY  = '#64748B'

function pct(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—'
  return `${v >= 0 ? '+' : ''}${(v * 100).toFixed(2)}%`
}

function num(v: number | null, digits = 2): string {
  if (v === null || v === undefined) return '—'
  return v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

function signColor(v: number | null | undefined): string {
  if (v === null || v === undefined || v === 0) return GREY
  return v > 0 ? GREEN : RED
}

/** A yield or volatility index moves in points, not percent — never show it as a return. */
function moveText(m: MarketMove): string {
  if (m.unit === 'level') {
    if (m.change === null || m.change === undefined) return '—'
    return `${m.change >= 0 ? '+' : ''}${m.change.toFixed(2)}`
  }
  return pct(m.return)
}

function Chip({ label, tone }: { label: string; tone?: 'up' | 'down' | 'flat' }) {
  const bg = tone === 'up' ? '#E6F9F1' : tone === 'down' ? '#FDECEE' : '#F1F5F9'
  const fg = tone === 'up' ? GREEN : tone === 'down' ? RED : '#475569'
  return (
    <span className="text-xs font-semibold px-2.5 py-1 rounded-lg" style={{ background: bg, color: fg }}>
      {label}
    </span>
  )
}

function SectorBar({ row, max }: { row: SectorRow; max: number }) {
  const width = max > 0 ? Math.min(100, (Math.abs(row.return) / max) * 100) : 0
  const up = row.return >= 0
  return (
    <div className="flex items-center gap-2 py-[3px]">
      <div className="w-44 shrink-0 text-xs text-slate-700 truncate">
        {row.sector}
        <span className="text-slate-400"> · {row.market === 'Canada' ? 'CA' : 'US'}</span>
      </div>
      <div className="flex-1 h-2 rounded-full bg-slate-100 overflow-hidden flex"
           style={{ justifyContent: up ? 'flex-start' : 'flex-end' }}>
        <div className="h-full rounded-full"
             style={{ width: `${width}%`, background: up ? GREEN : RED, opacity: 0.75 }} />
      </div>
      <div className="w-16 text-right text-xs font-semibold tabular-nums"
           style={{ color: signColor(row.return) }}>
        {pct(row.return)}
      </div>
    </div>
  )
}

function CrossTrends({ t }: { t: CrossWindowTrends }) {
  const groups: [string, typeof t.rotating_in, 'up' | 'down' | 'flat'][] = [
    ['Positive in every window', t.sustained_strength, 'up'],
    ['Negative in every window', t.sustained_weakness, 'down'],
    ['Turned up after a weak month', t.rotating_in, 'up'],
    ['Turned down after a strong month', t.rotating_out, 'down'],
  ]
  const shown = groups.filter(([, items]) => items && items.length > 0)
  if (shown.length === 0) return null

  return (
    <div className="rounded-xl border border-slate-200 p-5 mb-4" style={{ background: '#FAFCFE' }}>
      <h3 className="text-sm font-bold text-slate-900 mb-1">Trends across all periods</h3>
      <p className="text-xs text-slate-400 mb-3">
        {/* The framing matters: these are observations, not predictions. */}
        What has already happened across today, this week and this month — not a forecast.
      </p>
      <div className="space-y-2">
        {shown.map(([title, items, tone]) => (
          <div key={title} className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-slate-500 w-56 shrink-0">{title}</span>
            {items.map((i) => (
              <Chip key={`${i.sector}-${i.market}`}
                    label={`${i.sector} · ${i.market === 'Canada' ? 'CA' : 'US'}`}
                    tone={tone} />
            ))}
          </div>
        ))}
      </div>
    </div>
  )
}

function WindowCard({ win }: { win: MarketWindow }) {
  const [showBackdrop, setShowBackdrop] = useState(false)
  const sectors = win.sectors || []
  const t = win.trends || ({} as MarketWindow['trends'])
  const max = sectors.reduce((m, s) => Math.max(m, Math.abs(s.return)), 0)

  return (
    <div className="rounded-xl border border-slate-200 p-5">
      <div className="flex items-baseline justify-between mb-3">
        <h3 className="text-sm font-bold text-slate-900">{win.label}</h3>
        {!win.data_unavailable && (
          <span className="text-xs text-slate-400">{win.sentiment_label} news tone</span>
        )}
      </div>

      {/* Signals */}
      <div className="flex flex-wrap gap-2 mb-4">
        {t.breadth_label && (
          <Chip label={`${t.breadth_label} · ${((t.breadth ?? 0) * 100).toFixed(0)}% positive`}
                tone={(t.breadth ?? 0.5) > 0.55 ? 'up' : (t.breadth ?? 0.5) < 0.45 ? 'down' : 'flat'} />
        )}
        {t.risk_label && (
          <Chip label={t.risk_label}
                tone={t.risk_label.startsWith('Risk-on') ? 'up'
                    : t.risk_label.startsWith('Risk-off') ? 'down' : 'flat'} />
        )}
      </div>

      {/* Canada vs US splits — the reason both markets are tracked. */}
      {t.divergences && t.divergences.length > 0 && (
        <div className="mb-4 text-xs text-slate-600 space-y-1">
          {t.divergences.map((d) => (
            <div key={d.sector}>
              <span className="font-semibold">{d.sector}</span> split across borders:{' '}
              <span style={{ color: signColor(d.canada) }}>Canada {pct(d.canada)}</span>
              {' vs '}
              <span style={{ color: signColor(d.us) }}>US {pct(d.us)}</span>
            </div>
          ))}
        </div>
      )}

      {/* Sectors */}
      {sectors.length > 0 ? (
        <div className="mb-4">
          <p className="text-xs font-semibold text-slate-500 mb-1.5">Sectors, best to worst</p>
          {sectors.map((s) => <SectorBar key={s.symbol} row={s} max={max} />)}
        </div>
      ) : (
        <p className="text-sm text-slate-400 mb-4">Sector data unavailable.</p>
      )}

      {/* Commentary — only what the chart above can't show. Rendered only when
          there is something non-redundant to say; no stub, no restatement. */}
      {win.narrative && (
        <div className="mb-3">
          <p className="text-sm text-slate-700 leading-relaxed">{win.narrative}</p>
          <p className="text-xs text-slate-400 mt-2">
            {/* Readers should never have to guess whether this is analysis or a digest. */}
            {win.summarizer === 'claude'
              ? 'Written by Claude.'
              : 'Headline excerpt (no AI summariser configured).'}
            {win.sources.length > 0 && <> Sources: {win.sources.join(', ')}.</>}
          </p>
        </div>
      )}

      {/* Market backdrop */}
      <button
        type="button"
        onClick={() => setShowBackdrop((v) => !v)}
        className="text-xs font-medium hover:underline"
        style={{ color: GREEN }}
      >
        {showBackdrop ? 'Hide' : 'Show'} market backdrop
      </button>
      {showBackdrop && (
        <table className="w-full text-sm mt-2">
          <tbody>
            {Object.values(win.moves).map((m) => (
              <tr key={m.symbol} className="border-b border-slate-100 last:border-0">
                <td className="py-1.5 pr-3 text-slate-700">{m.name}</td>
                <td className="py-1.5 pr-3 text-right tabular-nums text-slate-500">{num(m.level)}</td>
                <td className="py-1.5 text-right tabular-nums font-semibold w-20"
                    style={{ color: signColor(m.unit === 'level' ? m.change : m.return) }}>
                  {moveText(m)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

export default function MarketSummaryPanel() {
  const [data, setData]       = useState<MarketSummary | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState<string | null>(null)

  async function load(refresh = false) {
    setLoading(true)
    setError(null)
    try {
      setData(await getMarketSummary(refresh))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load market summary')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  return (
    <div>
      <div className="flex items-baseline justify-between mb-5">
        <div>
          <h2 className="text-base font-bold text-slate-900">Market Summary</h2>
          <p className="text-xs text-slate-400 mt-0.5">
            {data
              ? `Sector performance and trends · as of ${new Date(data.as_of).toLocaleString()}`
              : 'Sector performance and trends'}
          </p>
        </div>
        <button
          type="button"
          onClick={() => load(true)}
          disabled={loading}
          className="text-sm font-medium hover:underline disabled:opacity-40"
          style={{ color: GREEN }}
        >
          Refresh
        </button>
      </div>

      {loading && <p className="text-sm text-slate-400">Loading sector data…</p>}

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {data && !loading && (
        <div>
          {data.cross_window_trends && <CrossTrends t={data.cross_window_trends} />}
          <div className="space-y-4">
            {WINDOW_ORDER.filter((k) => data.windows[k]).map((k) => (
              <WindowCard key={k} win={data.windows[k]} />
            ))}
          </div>
          {data.errors.length > 0 && (
            <p className="text-xs text-slate-400 mt-3">Notes: {data.errors.join('; ')}</p>
          )}
        </div>
      )}
    </div>
  )
}
