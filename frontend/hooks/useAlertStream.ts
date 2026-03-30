'use client'
import { useEffect, useRef } from 'react'
import { useAlertStore } from '@/stores/alertStore'
import { SSE_URL } from '@/lib/api'
import type { AlertSummary } from '@/types/alert'

const RECONNECT_DELAY = 5000

export function useAlertStream() {
  const addAlert = useAlertStore((s) => s.addAlert)
  const updateAlert = useAlertStore((s) => s.updateAlert)
  const setStreamStatus = useAlertStore((s) => s.setStreamStatus)
  const esRef = useRef<EventSource | null>(null)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    function connect() {
      if (esRef.current) {
        esRef.current.close()
      }
      setStreamStatus('connecting')
      const es = new EventSource(SSE_URL)
      esRef.current = es

      es.addEventListener('volume_alert', (e: MessageEvent) => {
        try {
          const alert = JSON.parse(e.data) as AlertSummary
          addAlert(alert)
        } catch {}
      })

      es.addEventListener('alert_status_update', (e: MessageEvent) => {
        try {
          const patch = JSON.parse(e.data) as Partial<AlertSummary> & { id: number }
          if (patch.id != null) updateAlert(patch.id, patch)
        } catch {}
      })

      es.onopen = () => setStreamStatus('connected')

      es.onerror = () => {
        setStreamStatus('disconnected')
        es.close()
        esRef.current = null
        reconnectTimer.current = setTimeout(connect, RECONNECT_DELAY)
      }
    }

    connect()

    return () => {
      if (esRef.current) esRef.current.close()
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
    }
  }, [addAlert, updateAlert, setStreamStatus])
}
