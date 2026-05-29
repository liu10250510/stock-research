import { useState } from 'react'
import ConfigForm from './components/ConfigForm'
import CustomSymbolsForm from './components/CustomSymbolsForm'
import ProgressPanel from './components/ProgressPanel'
import DownloadButton from './components/DownloadButton'
import { useJobStream } from './hooks/useJobStream'

type Tab = 'portfolio' | 'custom'

function TabButton({
  active, onClick, children,
}: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex-1 py-2 rounded-lg text-sm font-semibold transition-all"
      style={active
        ? { background: '#0EA472', color: '#fff', boxShadow: '0 2px 8px rgba(14,164,114,0.25)' }
        : { background: 'transparent', color: '#94A3B8' }
      }
    >
      {children}
    </button>
  )
}

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>('portfolio')
  const [jobId, setJobId]         = useState<string | null>(null)
  const [filename, setFilename]   = useState('stock_report.pdf')
  const { lines, status, error }  = useJobStream(jobId)

  function handleJobStarted(id: string, outputName: string) {
    setJobId(id)
    setFilename(outputName)
  }

  function switchTab(tab: Tab) {
    setActiveTab(tab)
    setJobId(null)   // reset progress panel when switching tabs
  }

  const isRunning = jobId !== null && (status === 'running' || status === 'pending')

  return (
    <div className="min-h-screen py-10 px-4" style={{ background: '#F0F4F8' }}>
      <div className="max-w-2xl mx-auto">

        {/* Header */}
        <div className="flex items-center justify-between mb-10">
          <div className="flex items-center gap-3">
            <div
              className="w-10 h-10 rounded-xl flex items-center justify-center text-white font-extrabold text-lg shrink-0"
              style={{ background: '#0EA472' }}
            >
              S
            </div>
            <div>
              <h1 className="text-[19px] font-bold tracking-tight text-slate-900">
                Stock Research Tool
              </h1>
              <p className="text-xs text-slate-400 mt-0.5">
                Canadian retail investor report generator
              </p>
            </div>
          </div>
          <span
            className="text-xs font-semibold px-3 py-1.5 rounded-lg"
            style={{ color: '#0EA472', background: '#E6F9F1' }}
          >
            Live data
          </span>
        </div>

        {/* Tab switcher */}
        <div
          className="flex gap-1 p-1 mb-7 rounded-xl border border-slate-200 bg-white"
          style={{ boxShadow: '0 1px 4px rgba(15,28,46,0.06)' }}
        >
          <TabButton active={activeTab === 'portfolio'} onClick={() => switchTab('portfolio')}>
            Portfolio Report
          </TabButton>
          <TabButton active={activeTab === 'custom'} onClick={() => switchTab('custom')}>
            Custom Analysis
          </TabButton>
        </div>

        {/* Card */}
        <div
          className="bg-white rounded-2xl border border-slate-200 p-10"
          style={{ boxShadow: '0 2px 16px rgba(15,28,46,0.06)' }}
        >
          {activeTab === 'portfolio'
            ? <ConfigForm onJobStarted={handleJobStarted} disabled={isRunning} />
            : <CustomSymbolsForm onJobStarted={handleJobStarted} disabled={isRunning} />
          }

          {jobId && (
            <ProgressPanel lines={lines} status={status} error={error} />
          )}

          {status === 'done' && jobId && (
            <DownloadButton jobId={jobId} filename={filename} />
          )}
        </div>

        {/* Reset */}
        {jobId && !isRunning && (
          <div className="mt-4 text-center">
            <button
              onClick={() => setJobId(null)}
              className="text-sm font-medium hover:underline"
              style={{ color: '#0EA472' }}
            >
              Run another analysis
            </button>
          </div>
        )}

        <p className="text-center text-xs text-slate-300 mt-8">
          Prices delayed ~15 min · For informational purposes only
        </p>
      </div>
    </div>
  )
}
