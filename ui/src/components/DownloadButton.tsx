import { getReportUrl } from '../api'

interface Props {
  jobId: string
  filename: string
}

export default function DownloadButton({ jobId, filename }: Props) {
  return (
    <div
      className="mt-8 flex items-center gap-4 p-4 rounded-2xl border"
      style={{ background: '#E6F9F1', borderColor: 'rgba(14,164,114,0.25)' }}
    >
      {/* Check icon */}
      <div
        className="w-10 h-10 rounded-full flex items-center justify-center shrink-0"
        style={{ background: '#0EA472' }}
      >
        <svg className="w-5 h-5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
        </svg>
      </div>

      <div className="flex-1 min-w-0">
        <p className="text-sm font-bold" style={{ color: '#065F46' }}>Report ready</p>
        <p className="text-xs truncate" style={{ color: '#0EA472' }}>{filename}</p>
      </div>

      <a
        href={getReportUrl(jobId)}
        download={filename}
        className="shrink-0 text-white text-sm font-bold px-5 py-2.5 rounded-xl transition-opacity hover:opacity-90 flex items-center gap-2"
        style={{ background: '#0EA472', boxShadow: '0 4px 12px rgba(14,164,114,0.3)' }}
      >
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M7 10l5 5 5-5M12 15V3" />
        </svg>
        Download PDF
      </a>
    </div>
  )
}
