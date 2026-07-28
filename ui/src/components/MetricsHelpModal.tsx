import { useEffect } from 'react'
import { METRIC_HELP } from '../content/metricsHelp'

const BRAND = '#0EA472'

interface Props {
  open: boolean
  onClose: () => void
}

export default function MetricsHelpModal({ open, onClose }: Props) {
  useEffect(() => {
    if (!open) return
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [open, onClose])

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: 'rgba(15,28,46,0.45)' }}
      onClick={onClose}
    >
      <div
        className="bg-white rounded-2xl border border-slate-200 w-full max-w-lg max-h-[80vh] flex flex-col"
        style={{ boxShadow: '0 8px 32px rgba(15,28,46,0.25)' }}
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-100 shrink-0">
          <h2 className="text-[15px] font-bold text-slate-900">Metric definitions</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="w-8 h-8 rounded-lg flex items-center justify-center text-slate-400 hover:bg-slate-100 hover:text-slate-600 transition-colors"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <dl className="px-6 py-4 overflow-y-auto space-y-5">
          {METRIC_HELP.map(m => (
            <div key={m.label}>
              <dt className="text-[13px] font-bold" style={{ color: BRAND }}>{m.label}</dt>
              <dd className="text-sm text-slate-600 mt-1 leading-relaxed">{m.description}</dd>
            </div>
          ))}
        </dl>
      </div>
    </div>
  )
}
