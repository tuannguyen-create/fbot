import type { AlertDetail, AlertListParams, AlertSummary, AlertTodaySummary } from '@/types/alert'
import type { CycleDetail, CycleSummary } from '@/types/cycle'
import type { AppSettings, HealthStatus, WatchlistItem } from '@/types/api'

const BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), 10_000)

  try {
    const res = await fetch(`${BASE_URL}${path}`, {
      ...options,
      signal: controller.signal,
      headers: { 'Content-Type': 'application/json', ...options?.headers },
    })
    const json = await res.json()
    // API contract: unwrap json.data (NOT json directly)
    if (!json.success) throw new Error(json.error ?? `HTTP ${res.status}`)
    return json.data as T
  } finally {
    clearTimeout(timeout)
  }
}

function buildQuery(params?: Record<string, unknown>): string {
  if (!params) return ''
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') {
      p.set(k, String(v))
    }
  }
  const s = p.toString()
  return s ? `?${s}` : ''
}

// ---- Alerts ----
export const alertsApi = {
  list: (params?: AlertListParams) =>
    apiFetch<{ alerts: AlertSummary[]; total: number; limit: number; offset: number }>(
      `/api/v1/alerts${buildQuery(params as Record<string, unknown>)}`
    ),
  get: (id: number) => apiFetch<{ alert: AlertDetail }>(`/api/v1/alerts/${id}`),
  summaryToday: () => apiFetch<AlertTodaySummary>(`/api/v1/alerts/summary/today`),
}

// ---- Cycles ----
export const cyclesApi = {
  list: (params?: { phase?: string; ticker?: string; limit?: number; offset?: number }) =>
    apiFetch<{ cycles: CycleSummary[]; total: number }>(
      `/api/v1/cycles${buildQuery(params as Record<string, unknown>)}`
    ),
  get: (id: number) => apiFetch<{ cycle: CycleDetail }>(`/api/v1/cycles/${id}`),
}

// ---- Watchlist ----
export const watchlistApi = {
  list: () => apiFetch<{ tickers: WatchlistItem[] }>(`/api/v1/watchlist`),
  summary: (ticker: string) =>
    apiFetch<{
      ticker: string
      company_name: string | null
      today_alerts: number
      active_cycle: CycleSummary | null
      alert_history: {
        total_30d: number
        confirmed_30d: number
        last_alerts: Pick<AlertSummary, 'id' | 'bar_time' | 'fired_at' | 'slot' | 'ratio_5d' | 'status' | 'in_magic_window'>[]
      }
    }>(`/api/v1/watchlist/${ticker}/summary`),
  updateM3: (ticker: string, body: { eligible_for_m3?: boolean; game_type?: string }) =>
    apiFetch<{ ticker: string; eligible_for_m3?: boolean; game_type?: string }>(
      `/api/v1/watchlist/${ticker}/m3`,
      { method: 'PATCH', body: JSON.stringify(body) }
    ),
}

// ---- Settings ----
export const settingsApi = {
  get: () => apiFetch<AppSettings>(`/api/v1/settings`),
  updateThresholds: (body: {
    threshold_normal?: number
    threshold_magic?: number
    threshold_confirm_15m?: number
  }) =>
    apiFetch<{ updated: boolean }>(`/api/v1/settings/thresholds`, {
      method: 'PUT',
      body: JSON.stringify(body),
    }),
}

// ---- Health ----
export const healthApi = {
  check: () => apiFetch<HealthStatus>(`/api/v1/health`),
}

// ---- SSE ----
export const SSE_URL = `${BASE_URL}/api/v1/stream/alerts`
