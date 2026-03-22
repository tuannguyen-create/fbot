'use client'
import { create } from 'zustand'
import type { AlertSummary } from '@/types/alert'

type StreamStatus = 'connected' | 'disconnected' | 'connecting'

interface AlertState {
  liveAlerts: AlertSummary[]
  streamStatus: StreamStatus
  lastAlertTime: string | null
  addAlert: (alert: AlertSummary) => void
  setStreamStatus: (s: StreamStatus) => void
}

export const useAlertStore = create<AlertState>()((set) => ({
  liveAlerts: [],
  streamStatus: 'connecting',
  lastAlertTime: null,
  addAlert: (alert) =>
    set((s) => ({
      liveAlerts: [alert, ...s.liveAlerts].slice(0, 50),
      lastAlertTime: alert.fired_at,
    })),
  setStreamStatus: (streamStatus) => set({ streamStatus }),
}))
