import { useEffect, useRef } from 'react'
import type { JobStatus } from '../types'

interface Props {
  lines: string[]
  status: JobStatus
  error: string | null
}

const BADGE: Record<JobStatus, { cls: string; label: string }> = {
  pending: { cls: 'bg-slate-100 text-slate-500',                   label: 'Pending' },
  running: { cls: 'bg-amber-50 text-amber-600',                    label: 'Running…' },
  done:    { cls: 'bg-[#E6F9F1] text-[#0EA472]',                  label: 'Complete' },
  error:   { cls: 'bg-red-50 text-red-600',                        label: 'Error' },
}

function colorLine(line: string, isLast: boolean, isRunning: boolean) {
  if (line.includes('✓')) return 'text-[#0EA472]'
  if (isLast && isRunning) return 'text-[#FEBC2E]'
  if (line.startsWith('\n') || line.trim() === '') return 'text-[#2A4060]'
  return 'text-[#C8D8E8]'
}

export default function ProgressPanel({ lines, status, error }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [lines])

  const isRunning = status === 'running'
  const badge = BADGE[status]

  return (
    <div className="mt-10">
      {/* Terminal window */}
      <div className="rounded-2xl overflow-hidden" style={{ background: '#0F1C2E' }}>
        {/* macOS-style title bar */}
        <div className="flex items-center gap-1.5 px-4 py-2.5" style={{ background: '#18293D' }}>
          <div className="w-2.5 h-2.5 rounded-full" style={{ background: '#FF5F57' }} />
          <div className="w-2.5 h-2.5 rounded-full" style={{ background: '#FEBC2E' }} />
          <div className="w-2.5 h-2.5 rounded-full" style={{ background: '#28C840' }} />
          <span className="text-[11px] ml-2" style={{ color: '#4A6080' }}>pipeline output</span>
        </div>

        {/* Log body */}
        <pre className="font-mono text-xs p-4 h-52 overflow-y-auto whitespace-pre-wrap leading-relaxed">
          {lines.length === 0
            ? <span style={{ color: '#2A4060' }}>Waiting for output…</span>
            : lines.map((line, i) => (
                <span key={i} className={colorLine(line, i === lines.length - 1, isRunning)}>
                  {line}{i === lines.length - 1 && isRunning ? '▌' : ''}{'\n'}
                </span>
              ))
          }
          <div ref={bottomRef} />
        </pre>
      </div>

      {/* Status row */}
      <div className="flex items-center justify-between mt-3 px-1">
        <span className={`inline-flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-full ${badge.cls}`}>
          {isRunning && (
            <span className="w-1.5 h-1.5 rounded-full animate-pulse" style={{ background: '#D97706' }} />
          )}
          {badge.label}
        </span>

        {status === 'error' && error && (
          <p className="text-xs text-red-500 max-w-xs truncate">{error}</p>
        )}
      </div>
    </div>
  )
}
