export interface AlertSummary {
  id: number
  ticker: string
  fired_at: string         // ISO UTC string
  slot: number             // 0-239
  volume: number
  ratio_5d: number | null
  bu_pct: number | null    // 0-100
  in_magic_window: boolean
  status: 'fired' | 'confirmed' | 'cancelled'
}

export interface AlertDetail extends AlertSummary {
  baseline_5d: number | null
  foreign_net: number | null
  confirmed_at: string | null
  ratio_15m: number | null
  email_sent: boolean
  cycle_event_id: number | null
}

export interface AlertListParams {
  ticker?: string
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
