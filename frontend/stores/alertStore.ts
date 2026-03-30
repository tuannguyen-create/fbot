'use client'
import { create } from 'zustand'
import type { AlertSummary } from '@/types/alert'

type StreamStatus = 'connected' | 'disconnected' | 'connecting'

interface AlertState {
  liveAlerts: AlertSummary[]
  streamStatus: StreamStatus
  lastAlertTime: string | null
  addAlert: (alert: AlertSummary) => void
  updateAlert: (id: number, patch: Partial<AlertSummary>) => void
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
  updateAlert: (id, patch) =>
    set((s) => ({
      liveAlerts: s.liveAlerts.map((alert) => (
        alert.id === id ? { ...alert, ...patch } : alert
      )),
    })),
  setStreamStatus: (streamStatus) => set({ streamStatus }),
}))
