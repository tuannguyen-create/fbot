export interface AlertSummary {
  id: number
  ticker: string
  fired_at: string         // ISO — when system recorded the alert (may differ from bar time)
  bar_time: string | null  // ISO — actual market bar time (slot time in ICT)
  slot: number             // 0-239
  volume: number
  ratio_5d: number | null
  bu_pct: number | null    // 0-100
  in_magic_window: boolean
  status: 'fired' | 'confirmed' | 'cancelled'
  quality_grade: 'A' | 'B' | 'C' | null
  origin: 'live' | 'historical_replay' | 'recovery_replay'
  is_actionable: boolean
}

export interface M1Features {
  body_pct: number
  upper_shadow_pct: number
  lower_shadow_pct: number
  close_pos: number
  is_green_candle: boolean
  strong_bull_candle: boolean
  avg_vol_20: number
  avg_vol_50: number
  range_pct: number
  is_sideways_base: boolean
  ma10: number | null
  ma20: number | null
  price_above_ma10: boolean | null
  ma_stack_up: boolean
  macd_hist: number | null
  macd_hist_rising: boolean | null
  candle_score: number
  base_score: number
  ma_score: number
  macd_score: number
  quality_score: number
  quality_reason: string
  quality_tags: string[]
}

export interface AlertDetail extends AlertSummary {
  baseline_5d: number | null
  foreign_net: number | null
  confirmed_at: string | null
  ratio_15m: number | null
  email_sent: boolean
  cycle_event_id: number | null
  features: M1Features | null
  quality_score: number | null
  quality_reason: string | null
  strong_bull_candle: boolean | null
  is_sideways_base: boolean | null
  replay_run_id: string | null
  replayed_at: string | null
}

export interface AlertListParams {
  ticker?: string
  origin?: 'live' | 'historical_replay' | 'recovery_replay'
  date_from?: string
  date_to?: string
  status?: string
  magic_only?: boolean
  limit?: number
  offset?: number
}

export interface AlertTodaySummary {
  total: number
  confirmed: number
  fired: number
  cancelled: number
  by_ticker: Record<string, number>
}
