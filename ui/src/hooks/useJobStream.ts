import { useEffect, useRef, useState } from 'react'
import type { JobStatus } from '../types'

const MAX_RECONNECTS = 60
const RECONNECT_DELAY_MS = 3000

export function useJobStream(jobId: string | null) {
  const [lines, setLines] = useState<string[]>([])
  const [status, setStatus] = useState<JobStatus>('pending')
  const [error, setError] = useState<string | null>(null)

  const esRef         = useRef<EventSource | null>(null)
  const timerRef      = useRef<ReturnType<typeof setTimeout> | null>(null)
  const reconnectsRef = useRef(0)
  const receivedRef   = useRef(0)   // lines already shown — skip on replay
  const replaySkipRef = useRef(0)   // how many replay lines to skip after reconnect

  useEffect(() => {
    if (!jobId) return

    setLines([])
    setStatus('running')
    setError(null)
    reconnectsRef.current = 0
    receivedRef.current   = 0
    replaySkipRef.current = 0

    function connect() {
      const es = new EventSource(`/api/jobs/${jobId}/stream`)
      esRef.current = es

      es.onmessage = (evt: MessageEvent<string>) => {
        const data = evt.data

        // Successful message — reset reconnect counter
        reconnectsRef.current = 0

        if (data === '[DONE]') {
          setStatus('done')
          es.close()
          return
        }

        if (data.startsWith('[ERROR]')) {
          setStatus('error')
          setError(data.replace('[ERROR] ', '') || 'Unknown error')
          es.close()
          return
        }

        // Skip lines already displayed (backend replays from start on reconnect)
        if (replaySkipRef.current > 0) {
          replaySkipRef.current--
          return
        }

        receivedRef.current++
        setLines(prev => [...prev, data])
      }

      es.onerror = () => {
        es.close()
        if (reconnectsRef.current < MAX_RECONNECTS) {
          reconnectsRef.current++
          replaySkipRef.current = receivedRef.current   // skip already-shown lines
          timerRef.current = setTimeout(connect, RECONNECT_DELAY_MS)
        } else {
          setStatus('error')
          setError('Connection to server lost')
        }
      }
    }

    connect()

    return () => {
      if (timerRef.current) clearTimeout(timerRef.current)
      esRef.current?.close()
    }
  }, [jobId])

  return { lines, status, error }
}
