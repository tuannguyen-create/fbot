export type Phase =
  | 'distribution_in_progress'
  | 'bottoming_candidate'
  | 'invalidated'
  | 'done'

export interface CycleSummary {
  id: number
  ticker: string
  breakout_date: string          // YYYY-MM-DD
  phase: Phase
  days_remaining: number | null
  trading_days_elapsed: number | null
  estimated_dist_days: number | null
  // meeting-goc v1.5 fields
  game_type: string | null
  rewatch_window_start: string | null
  rewatch_window_end: string | null
  phase_reason: string | null
  invalidation_reason: string | null
  breakout_zone_low: number | null
  breakout_zone_high: number | null
  /** @deprecated use rewatch_window_start instead */
  predicted_bottom_date: string | null
  source_alert_id: number | null
  /** FALSE = canonical (M1→M3 intraday path); TRUE = heuristic backfill from migration */
  source_alert_inferred: boolean
  origin: 'live' | 'historical_replay' | 'recovery_replay'
}

export interface CycleDetail extends CycleSummary {
  peak_volume: number | null
  breakout_price: number | null
  alert_sent_10d: boolean
  alert_sent_bottom: boolean
  breakout_email_sent: boolean
  replayed_at: string | null
  created_at: string
  updated_at: string
}
