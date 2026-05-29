import { useEffect, useRef, useState } from 'react'
import type { JobStatus } from '../types'

export function useJobStream(jobId: string | null) {
  const [lines, setLines] = useState<string[]>([])
  const [status, setStatus] = useState<JobStatus>('pending')
  const [error, setError] = useState<string | null>(null)
  const esRef = useRef<EventSource | null>(null)

  useEffect(() => {
    if (!jobId) return

    setLines([])
    setStatus('running')
    setError(null)

    const es = new EventSource(`/api/jobs/${jobId}/stream`)
    esRef.current = es

    es.onmessage = (evt: MessageEvent<string>) => {
      const data = evt.data
      if (data === '[DONE]') {
        setStatus('done')
        es.close()
      } else if (data.startsWith('[ERROR]')) {
        setStatus('error')
        setError(data.replace('[ERROR] ', '') || 'Unknown error')
        es.close()
      } else {
        setLines(prev => [...prev, data])
      }
    }

    es.onerror = () => {
      setStatus('error')
      setError('Connection to server lost')
      es.close()
    }

    return () => {
      es.close()
    }
  }, [jobId])

  return { lines, status, error }
}
