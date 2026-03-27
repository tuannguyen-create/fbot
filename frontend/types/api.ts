export interface ApiResponse<T> {
  success: boolean
  data?: T
  error?: string
}

export interface WatchlistItem {
  ticker: string
  company_name: string | null
  exchange: string
  sector: string | null
  in_vn30: boolean
  active: boolean
  eligible_for_m3: boolean
  game_type: string | null
}

export interface AppSettings {
  threshold_normal: number
  threshold_magic: number
  threshold_confirm_15m: number
  breakout_vol_mult: number
  breakout_price_pct: number
  alert_days_before_cycle: number
  active_ticker_count: number
  effective_ticker_count: number
  fiinquant_ticker_limit: number
  stream_status: 'connected' | 'disconnected'
}

export interface HealthStatus {
  db: 'ok' | 'error'
  redis: 'ok' | 'error' | 'disabled'
  stream: 'connected' | 'disconnected'
  stream_reason: 'outside_hours' | 'connecting' | 'reconnecting' | 'error' | null
  last_bar_time: string | null
  timestamp: string
}
